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
import formatting
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


def _is_target_channel(chat) -> bool:
    """True kalau `chat` adalah channel yang dikonfigurasi di
    TELEGRAM_CHANNEL_ID. Dipakai supaya kalau bot ini pernah/nanti jadi
    admin di channel LAIN (mis. buat testing), post di channel lain itu
    tidak ikut diparse jadi signal / disimpan ke database.

    TELEGRAM_CHANNEL_ID bisa berupa:
    - numeric chat id, mis. "-1001234567890" (channel private)
    - "@username" (channel public)
    """
    configured = str(config.TELEGRAM_CHANNEL_ID).strip()
    if configured.startswith("@"):
        return bool(chat.username) and f"@{chat.username}".lower() == configured.lower()
    return str(chat.id) == configured


def _is_admin(user_id: int | None) -> bool:
    """True kalau user_id ada di daftar TELEGRAM_ADMIN_IDS. Dipakai untuk
    membatasi command yang bisa mengubah state (cancel/close) atau memicu
    kirim pesan ke channel (rekap manual), supaya tidak sembarang orang
    yang bisa chat ke bot (mis. di grup) bisa membatalkan/menutup posisi."""
    return user_id is not None and user_id in config.TELEGRAM_ADMIN_IDS


async def _reject_non_admin(update: Update) -> bool:
    """Kalau pemanggil bukan admin: kirim penolakan & return True (supaya
    caller langsung `return`). Kalau admin: return False (lanjut proses)."""
    user = update.effective_user
    if _is_admin(user.id if user else None):
        return False
    await update.effective_message.reply_text(
        "🚫 Command ini cuma bisa dipakai admin yang terdaftar (TELEGRAM_ADMIN_IDS)."
    )
    logger.warning(
        "Percobaan akses command sensitif ditolak: user_id=%s username=%s",
        user.id if user else None, user.username if user else None,
    )
    return True


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dipanggil setiap ada post baru di channel (termasuk post kamu sendiri,
    dan post yang di-forward, karena bot jadi admin di channel)."""
    msg = update.effective_message
    if msg is None:
        return

    if not _is_target_channel(update.effective_chat):
        logger.info(
            "Post dari channel lain (chat_id=%s, username=%s) diabaikan — "
            "bukan TELEGRAM_CHANNEL_ID yang dikonfigurasi.",
            update.effective_chat.id, update.effective_chat.username,
        )
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
            formatting.duplicate_signal(parsed, dup),
            parse_mode="HTML",
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

    # Signal lain (selain yang baru saja disimpan) yang masih PENDING/ACTIVE
    # di pair yang sama, meskipun entry/SL/direction beda -> bukan duplikat
    # persis, tapi tetap perlu di-flag supaya kelihatan kalau ada 2 signal
    # nimpa di pair yang sama (mis. kamu & temanmu posting bareng).
    conflicts = [
        s for s in database.get_open_signals_by_symbol(parsed.symbol)
        if s["id"] != row["id"]
    ]
    if conflicts:
        logger.info(
            "Message_id=%s: ada %d signal lain yang masih terbuka di symbol %s.",
            msg.message_id, len(conflicts), parsed.symbol,
        )

    await msg.reply_text(
        formatting.new_signal(parsed, conflicts),
        parse_mode="HTML",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status -> snapshot posisi yang lagi dipantau (PENDING/ACTIVE),
    dengan tampilan yang sama seperti section snapshot di rekap harian."""
    open_signals = database.get_open_signals()
    targets_by_signal = database.get_targets_for_signals([s["id"] for s in open_signals])
    for chunk in recap.status_snapshot_chunks(open_signals, targets_by_signal):
        await update.effective_message.reply_text(
            chunk, parse_mode="HTML", disable_web_page_preview=True
        )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel $PAIR -> batalkan posisi yang masih PENDING (belum entry kesentuh).
    Hanya bisa dipanggil admin (lihat TELEGRAM_ADMIN_IDS)."""
    if await _reject_non_admin(update):
        return

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

    await update.effective_message.reply_text(
        formatting.cancelled(display_pair, pending),
        parse_mode="HTML",
    )

    # Umumkan juga ke channel supaya member tahu signal ini dibatalkan —
    # kecuali kalau /cancel memang dipanggil langsung dari channel itu
    # sendiri (reply_text di atas sudah otomatis muncul di sana).
    if str(update.effective_chat.id) != str(config.TELEGRAM_CHANNEL_ID):
        await context.bot.send_message(
            chat_id=config.TELEGRAM_CHANNEL_ID,
            text=formatting.cancel_channel_notice(pending),
            parse_mode="HTML",
        )


async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/close $PAIR -> tutup posisi ACTIVE di harga sekarang, RR dihitung dari harga tutup.
    Hanya bisa dipanggil admin (lihat TELEGRAM_ADMIN_IDS)."""
    if await _reject_non_admin(update):
        return

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

    closed = []
    for s in active:
        realized_rr = rr_calc.compute_manual_rr(s["direction"], s["entry"], s["stoploss"], curr)
        database.close_signal(s["id"], result="MANUAL", price=curr, realized_rr=realized_rr)
        closed.append({
            "pair": s["pair"],
            "direction": s["direction"],
            "price": curr,
            "rr": realized_rr,
        })
    await update.effective_message.reply_text(
        formatting.closed_manual(closed),
        parse_mode="HTML",
    )

    # Umumkan juga ke channel supaya member tahu pair ini sudah ditutup —
    # kecuali kalau /close memang dipanggil langsung dari channel itu
    # sendiri (reply_text di atas sudah otomatis muncul di sana).
    if str(update.effective_chat.id) != str(config.TELEGRAM_CHANNEL_ID):
        await context.bot.send_message(
            chat_id=config.TELEGRAM_CHANNEL_ID,
            text=formatting.close_channel_notice(closed),
            parse_mode="HTML",
        )


async def cmd_rekap_harian(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger manual rekap harian ke channel. Hanya admin, karena ini
    mengirim pesan ke channel publik/anggota."""
    if await _reject_non_admin(update):
        return
    await recap.send_daily_recap(context.bot)


async def cmd_rekap_bulanan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger manual rekap bulanan ke channel. Hanya admin."""
    if await _reject_non_admin(update):
        return
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
