"""Loop periodik yang cek harga MEXC dan update status signal:

PENDING -> ACTIVE   (harga menyentuh level entry)
ACTIVE  -> tiap level TP (TP1, TP2, TP3, ...) dicek satu-satu; begitu
           tersentuh, level itu ditandai HIT dan bot kirim notifikasi partial.
ACTIVE  -> CLOSED   terjadi kalau salah satu dari ini kejadian duluan:
           a) SEMUA level TP sudah HIT (termasuk yang baru saja HIT)
              -> result = WIN
           b) Stoploss kena SEBELUM semua TP tercapai
              -> result = LOSS (belum ada TP kena) atau MIXED (sebagian TP
                 sudah kena sebelum SL akhirnya kena)

Deteksi "menyentuh" pakai crossing check antara harga poll sebelumnya
dan harga sekarang, supaya tetap terdeteksi walau level ada di atas
ATAU di bawah harga saat signal diposting.
"""
import logging

import config
import database
import mexc_client

logger = logging.getLogger(__name__)


def _crossed(prev: float | None, curr: float, level: float) -> bool:
    if prev is None:
        # Belum ada harga sebelumnya untuk dibandingkan -> baru bisa akurat
        # mulai poll berikutnya lewat crossing check.
        return False
    return (prev - level) * (curr - level) <= 0


async def check_positions(bot, channel_id):
    open_signals = database.get_open_signals()
    if not open_signals:
        return

    try:
        all_prices = await mexc_client.get_all_prices()
    except Exception as e:
        logger.warning("Gagal ambil harga MEXC: %s", e)
        return

    active_signals = [s for s in open_signals if s["status"] == "ACTIVE"]
    targets_by_signal = database.get_targets_for_signals([s["id"] for s in active_signals])

    for sig in open_signals:
        symbol = sig["symbol"]
        curr = all_prices.get(symbol)
        if curr is None:
            continue

        prev = sig.get("last_price")

        if sig["status"] == "PENDING":
            if _crossed(prev, curr, sig["entry"]):
                database.mark_active(sig["id"], curr)
                await bot.send_message(
                    chat_id=channel_id,
                    text=(
                        f"🎯 ENTRY HIT — {sig['pair']} ({sig['direction']})\n"
                        f"Entry: {sig['entry']}\n"
                        f"Harga sekarang: {curr}"
                    ),
                )
            else:
                database.update_last_price(sig["id"], curr)
            continue

        # status == ACTIVE
        all_targets = sorted(targets_by_signal.get(sig["id"], []), key=lambda t: t["level"])
        pending_targets = [t for t in all_targets if t["status"] == "PENDING"]

        hit_sl = _crossed(prev, curr, sig["stoploss"])
        newly_hit_ids = set()

        for t in pending_targets:
            if _crossed(prev, curr, t["price"]):
                database.mark_target_hit(t["id"], curr)
                newly_hit_ids.add(t["id"])
                await bot.send_message(
                    chat_id=channel_id,
                    text=(
                        f"✅ TP{t['level']} HIT — {sig['pair']} ({sig['direction']})\n"
                        f"RR 1:{t['rr']:g} tercapai @ {curr}"
                    ),
                )

        remaining_pending = [t for t in pending_targets if t["id"] not in newly_hit_ids]
        all_tp_done = len(remaining_pending) == 0

        if all_tp_done:
            database.close_signal(sig["id"], result="WIN", price=curr)
            highest = all_targets[-1] if all_targets else None
            rr_text = f"RR 1:{highest['rr']:g}" if highest else ""
            await bot.send_message(
                chat_id=channel_id,
                text=(
                    f"🏁 POSISI CLOSED (WIN) — {sig['pair']} ({sig['direction']})\n"
                    f"Semua target TP tercapai ({rr_text})"
                ),
            )
        elif hit_sl:
            already_hit = [t for t in all_targets if t["status"] == "HIT" or t["id"] in newly_hit_ids]
            result = "MIXED" if already_hit else "LOSS"
            database.close_signal(sig["id"], result=result, price=curr)
            note = (
                f"Sebagian TP sudah tercapai sebelumnya ({len(already_hit)}/{len(all_targets)} level)"
                if result == "MIXED"
                else "Belum ada TP tercapai"
            )
            await bot.send_message(
                chat_id=channel_id,
                text=(
                    f"🛑 STOPLOSS HIT — {sig['pair']} ({sig['direction']})\n"
                    f"Entry: {sig['entry']} -> SL: {sig['stoploss']}\n"
                    f"{note}"
                ),
            )
        else:
            database.update_last_price(sig["id"], curr)


async def monitor_job(context):
    """Callback untuk python-telegram-bot JobQueue (run_repeating)."""
    await check_positions(context.bot, config.TELEGRAM_CHANNEL_ID)
