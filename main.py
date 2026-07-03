"""Bot Telegram untuk monitoring signal trading (entry/SL/TP) + rekap
harian & bulanan, harga dari MEXC, data disimpan di Supabase.

Cara pakai:
1. Isi .env (lihat .env.example)
2. Jalankan schema.sql di Supabase SQL Editor
3. Tambahkan bot sebagai ADMIN di channel Telegram kamu
4. python main.py
"""
import datetime
import logging

import pytz
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, CommandHandler, filters

import config
import database
import mexc_client
import monitor
import recap
import rr_calc
from signal_parser import parse_signal, parse_pair_arg

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dipanggil setiap ada post baru di channel (termasuk post kamu sendiri,
    dan post yang di-forward, karena bot jadi admin di channel)."""
    msg = update.effective_message
    if msg is None:
        return

    # Teks bisa ada di dua tempat tergantung tipe pesan:
    # - msg.text     -> pesan teks murni
    # - msg.caption  -> pesan foto/gambar (mis. screenshot chart) dengan keterangan teks
    text = msg.text or msg.caption
    if not text:
        logger.info(
            "Post baru di channel (message_id=%s) tidak punya teks/caption, dilewati.",
            msg.message_id,
        )
        return

    logger.info("Post baru diterima (message_id=%s): %.80s...", msg.message_id, text)

    parsed = parse_signal(text)
    if parsed is None:
        logger.info(
            "Message_id=%s tidak cocok format signal, diabaikan (pengumuman/pesan lain).",
            msg.message_id,
        )
        return  # bukan format signal, abaikan (pengumuman dll)

    dup = database.find_duplicate_open_signal(
        symbol=parsed.symbol,
        direction=parsed.direction,
        entry=parsed.entry,
        stoploss=parsed.stoploss,
    )
    if dup is not None:
        logger.info(
            "Message_id=%s adalah duplikat signal id=%s (status=%s), diabaikan.",
            msg.message_id, dup["id"], dup["status"],
        )
        await msg.reply_text(
            f"⚠️ Signal ini sama persis dengan signal yang masih {dup['status']}\n"
            f"{parsed.pair} ({parsed.direction}) | Entry {parsed.entry} | SL {parsed.stoploss}\n"
            f"Diabaikan supaya tidak tercatat dobel."
        )
        return

    row = database.insert_signal(
        message_id=msg.message_id,
        chat_id=msg.chat_id,
        pair=parsed.pair,
        symbol=parsed.symbol,
        direction=parsed.direction,
        entry=parsed.entry,
        stoploss=parsed.stoploss,
        raw_message=text,
    )
    database.insert_targets(row["id"], parsed.targets)
    logger.info(
        "Signal baru tersimpan: %s (targets: %s)",
        row,
        [(t.level, t.rr, t.price) for t in parsed.targets],
    )

    tp_lines = "\n".join(f"TP{t.level} (RR 1:{t.rr:g}): {t.price:g}" for t in parsed.targets)
    await msg.reply_text(
        f"📥 Signal tercatat & mulai dipantau\n"
        f"{parsed.pair} ({parsed.direction})\n"
        f"Entry: {parsed.entry} | SL: {parsed.stoploss}\n"
        f"{tp_lines}"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status -> list posisi yang lagi dipantau (PENDING/ACTIVE)."""
    open_signals = database.get_open_signals()
    if not open_signals:
        await update.effective_message.reply_text("Tidak ada posisi yang sedang dipantau saat ini.")
        return

    targets_by_signal = database.get_targets_for_signals([s["id"] for s in open_signals])

    lines = ["📋 Posisi yang sedang dipantau:\n"]
    for s in open_signals:
        targets = sorted(targets_by_signal.get(s["id"], []), key=lambda t: t["level"])
        tp_summary = ", ".join(
            f"TP{t['level']}{'✅' if t['status'] == 'HIT' else ''} {t['price']:g}" for t in targets
        )
        lines.append(
            f"{s['status']} — {s['pair']} ({s['direction']}) | "
            f"Entry {s['entry']} SL {s['stoploss']} | {tp_summary}"
        )
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel $PAIR -> batalkan posisi yang masih PENDING (belum entry kesentuh)."""
    if not context.args:
        await update.effective_message.reply_text(
            "Format: /cancel $PAIR (mis. /cancel $RE atau /cancel RE/USDT)"
        )
        return

    parsed_pair = parse_pair_arg(" ".join(context.args))
    if parsed_pair is None:
        await update.effective_message.reply_text("Format pair tidak dikenali.")
        return
    display_pair, symbol = parsed_pair

    pending = database.get_pending_signals_by_symbol(symbol)
    if not pending:
        active = database.get_active_signals_by_symbol(symbol)
        if active:
            await update.effective_message.reply_text(
                f"{display_pair} sudah ACTIVE (entry sudah kesentuh), tidak bisa di-cancel. "
                f"Pakai /close kalau mau ditutup manual sekarang."
            )
        else:
            await update.effective_message.reply_text(f"Tidak ada posisi PENDING untuk {display_pair}.")
        return

    for s in pending:
        database.cancel_signal(s["id"])

    names = "\n".join(f"• {s['pair']} ({s['direction']}) entry {s['entry']}" for s in pending)
    await update.effective_message.reply_text(f"🚫 Posisi PENDING dibatalkan:\n{names}")


async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/close $PAIR -> tutup posisi ACTIVE di harga sekarang, RR dihitung dari harga tutup."""
    if not context.args:
        await update.effective_message.reply_text("Format: /close $PAIR (mis. /close $RE)")
        return

    parsed_pair = parse_pair_arg(" ".join(context.args))
    if parsed_pair is None:
        await update.effective_message.reply_text("Format pair tidak dikenali.")
        return
    display_pair, symbol = parsed_pair

    active = database.get_active_signals_by_symbol(symbol)
    if not active:
        pending = database.get_pending_signals_by_symbol(symbol)
        if pending:
            await update.effective_message.reply_text(
                f"{display_pair} masih PENDING (belum entry kesentuh). "
                f"Pakai /cancel kalau mau dibatalkan."
            )
        else:
            await update.effective_message.reply_text(f"Tidak ada posisi ACTIVE untuk {display_pair}.")
        return

    try:
        curr = await mexc_client.get_price(symbol)
    except Exception as e:
        logger.warning("Gagal ambil harga MEXC untuk /close %s: %s", symbol, e)
        await update.effective_message.reply_text(f"Gagal ambil harga MEXC untuk {display_pair}.")
        return
    if curr is None:
        await update.effective_message.reply_text(f"Gagal ambil harga untuk {symbol}.")
        return

    lines = ["🔧 Posisi ditutup manual:"]
    for s in active:
        realized_rr = rr_calc.compute_manual_rr(s["direction"], s["entry"], s["stoploss"], curr)
        database.close_signal(s["id"], result="MANUAL", price=curr, realized_rr=realized_rr)
        lines.append(
            f"• {s['pair']} ({s['direction']}) @ {curr:g} — {realized_rr:+.2f}R"
        )
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_rekap_harian(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await recap.send_daily_recap(context.bot)


async def cmd_rekap_bulanan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await recap.send_monthly_recap(context.bot)


def main():
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Tangkap semua post di channel (bot harus jadi admin channel).
    # Termasuk pesan teks murni MAUPUN foto/gambar dengan caption teks
    # (mis. screenshot chart dengan keterangan signal).
    app.add_handler(
        MessageHandler(
            filters.ChatType.CHANNEL & (filters.TEXT | filters.CAPTION),
            handle_channel_post,
        )
    )


    # Command manual (dipanggil dari chat pribadi ke bot, atau di grup/channel)
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("close", cmd_close))
    app.add_handler(CommandHandler("rekap_harian", cmd_rekap_harian))
    app.add_handler(CommandHandler("rekap_bulanan", cmd_rekap_bulanan))

    job_queue = app.job_queue

    # Loop cek harga MEXC tiap N detik
    job_queue.run_repeating(monitor.monitor_job, interval=config.POLL_INTERVAL_SECONDS, first=5)

    # Rekap harian jam tertentu tiap hari
    tz = pytz.timezone(config.TIMEZONE)
    daily_time = datetime.time(hour=config.DAILY_RECAP_HOUR, minute=config.DAILY_RECAP_MINUTE, tzinfo=tz)
    job_queue.run_daily(recap.daily_recap_job, time=daily_time)

    # Rekap bulanan: dicek tiap hari jam tertentu, tapi cuma kirim kalau
    # tanggal hari ini == MONTHLY_RECAP_DAY (job_queue tidak punya run_monthly bawaan)
    async def monthly_check_job(context: ContextTypes.DEFAULT_TYPE):
        now_local = datetime.datetime.now(tz)
        if now_local.day == config.MONTHLY_RECAP_DAY:
            await recap.send_monthly_recap(context.bot)

    monthly_time = datetime.time(hour=config.MONTHLY_RECAP_HOUR, minute=config.MONTHLY_RECAP_MINUTE, tzinfo=tz)
    job_queue.run_daily(monthly_check_job, time=monthly_time)

    logger.info("Bot berjalan...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
