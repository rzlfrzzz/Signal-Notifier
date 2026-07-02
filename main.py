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
import monitor
import recap
from signal_parser import parse_signal

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dipanggil setiap ada post baru di channel (termasuk post kamu sendiri,
    karena bot jadi admin di channel)."""
    msg = update.effective_message
    if msg is None or not msg.text:
        return

    parsed = parse_signal(msg.text)
    if parsed is None:
        return  # bukan format signal, abaikan (pengumuman dll)

    row = database.insert_signal(
        message_id=msg.message_id,
        chat_id=msg.chat_id,
        pair=parsed.pair,
        symbol=parsed.symbol,
        direction=parsed.direction,
        entry=parsed.entry,
        stoploss=parsed.stoploss,
        take_profit=parsed.take_profit,
        rr=parsed.rr,
        raw_message=msg.text,
    )
    logger.info("Signal baru tersimpan: %s", row)

    await msg.reply_text(
        f"📥 Signal tercatat & mulai dipantau\n"
        f"{parsed.pair} ({parsed.direction})\n"
        f"Entry: {parsed.entry} | SL: {parsed.stoploss} | TP: {parsed.take_profit:g} "
        f"(RR 1:{parsed.rr:g})"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status -> list posisi yang lagi dipantau (PENDING/ACTIVE)."""
    open_signals = database.get_open_signals()
    if not open_signals:
        await update.effective_message.reply_text("Tidak ada posisi yang sedang dipantau saat ini.")
        return

    lines = ["📋 Posisi yang sedang dipantau:\n"]
    for s in open_signals:
        lines.append(
            f"{s['status']} — {s['pair']} ({s['direction']}) | "
            f"Entry {s['entry']} SL {s['stoploss']} TP {s['take_profit']:g}"
        )
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_rekap_harian(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await recap.send_daily_recap(context.bot)


async def cmd_rekap_bulanan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await recap.send_monthly_recap(context.bot)


def main():
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Tangkap semua post di channel (bot harus jadi admin channel)
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.TEXT, handle_channel_post))

    # Command manual (dipanggil dari chat pribadi ke bot, atau di grup/channel)
    app.add_handler(CommandHandler("status", cmd_status))
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
