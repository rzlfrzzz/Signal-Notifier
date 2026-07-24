"""Loop periodik yang cek harga MEXC dan update status signal:

PENDING -> ACTIVE       (harga menyentuh level entry)
PENDING -> INVALIDATED  (harga sudah menyentuh TP terjauh duluan, padahal
                          entry belum pernah kesentuh sama sekali -> entry
                          dianggap sudah "ketinggalan", signal dibatalkan
                          otomatis + notifikasi ke channel)
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
import formatting
import mexc_client
import rr_calc

logger = logging.getLogger(__name__)

# Signal id yang sudah pernah dapat notifikasi satu kali terkait sumber
# harganya: entah karena symbol-nya tidak ketemu di Spot MAUPUN Futures
# MEXC (warning ke admin), atau karena symbol-nya cuma ada di Futures jadi
# dipantau pakai harga Futures (info log saja). In-memory saja (reset
# kalau proses restart) — cukup untuk mencegah notifikasi spam tiap poll
# (default tiap 10 detik) selama proses jalan terus.
_notified_signal_ids: set[int] = set()


async def _notify_admins(bot, text: str):
    """Kirim notifikasi ke semua admin (TELEGRAM_ADMIN_IDS) via DM. Kalau
    seorang admin belum pernah /start bot ini secara pribadi, Telegram
    akan reject pengiriman — di-skip diam-diam per admin (tidak boleh
    bikin admin lain gagal kebagian notifikasi juga)."""
    for admin_id in config.TELEGRAM_ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning("Gagal kirim notifikasi ke admin %s: %s", admin_id, e)


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
        all_prices, futures_only_symbols = await mexc_client.get_combined_prices()
    except Exception as e:
        logger.warning("Gagal ambil harga MEXC: %s", e)
        return

    targets_by_signal = database.get_targets_for_signals([s["id"] for s in open_signals])

    for sig in open_signals:
        symbol = sig["symbol"]
        curr = all_prices.get(symbol)
        if curr is None:
            if sig["id"] not in _notified_signal_ids:
                _notified_signal_ids.add(sig["id"])
                logger.warning(
                    "Symbol %s (%s) tidak ditemukan di Spot maupun Futures MEXC — "
                    "posisi ini tidak akan pernah kedeteksi entry/TP/SL-nya.",
                    symbol, sig.get("pair"),
                )
                await _notify_admins(
                    bot,
                    f"⚠️ <b>Symbol tidak ditemukan di MEXC</b>\n"
                    f"Pair: <b>{sig.get('pair', symbol)}</b> (symbol: <code>{symbol}</code>)\n\n"
                    f"Sudah dicoba di Spot & Futures MEXC, tapi symbol ini tidak ada "
                    f"di keduanya. Kemungkinan salah ketik ticker di signal, atau "
                    f"pair-nya memang belum/tidak listing di MEXC. Posisi ini tidak "
                    f"akan terpantau otomatis sampai masalahnya diperbaiki (cek "
                    f"ulang ticker-nya, atau /cancel kalau memang salah).",
                )
            continue
        if symbol in futures_only_symbols and sig["id"] not in _notified_signal_ids:
            # Tandai juga di set yang sama supaya notifikasi "pakai harga
            # Futures" ini cuma dikirim SEKALI per signal, bukan tiap poll.
            _notified_signal_ids.add(sig["id"])
            logger.info(
                "Symbol %s (%s) tidak ada di Spot, dipantau pakai harga Futures MEXC.",
                symbol, sig.get("pair"),
            )

        prev = sig.get("last_price")

        if sig["status"] == "PENDING":
            if _crossed(prev, curr, sig["entry"]):
                database.mark_active(sig["id"], curr)
                await bot.send_message(
                    chat_id=channel_id,
                    text=formatting.entry_hit(sig, curr),
                    parse_mode="HTML",
                )
                continue

            sig_targets = sorted(targets_by_signal.get(sig["id"], []), key=lambda t: t["level"])
            last_target = sig_targets[-1] if sig_targets else None

            if last_target is not None and _crossed(prev, curr, last_target["price"]):
                database.invalidate_signal(sig["id"], curr)
                await bot.send_message(
                    chat_id=channel_id,
                    text=formatting.invalidated(sig, last_target, curr),
                    parse_mode="HTML",
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
                    text=formatting.tp_hit(sig, t, curr),
                    parse_mode="HTML",
                )

        remaining_pending = [t for t in pending_targets if t["id"] not in newly_hit_ids]
        all_tp_done = len(remaining_pending) == 0

        if all_tp_done:
            fresh_targets = database.get_all_targets_for_signal(sig["id"])
            realized_rr = rr_calc.compute_realized_rr("WIN", fresh_targets)
            database.close_signal(sig["id"], result="WIN", price=curr, realized_rr=realized_rr)
            highest = all_targets[-1] if all_targets else None
            rr_text = f"RR 1:{highest['rr']:g}" if highest else ""
            await bot.send_message(
                chat_id=channel_id,
                text=formatting.closed_win(sig, rr_text),
                parse_mode="HTML",
            )
        elif hit_sl:
            fresh_targets = database.get_all_targets_for_signal(sig["id"])
            already_hit = [t for t in fresh_targets if t["status"] == "HIT"]
            result = "MIXED" if already_hit else "LOSS"
            realized_rr = rr_calc.compute_realized_rr(result, fresh_targets)
            database.close_signal(sig["id"], result=result, price=curr, realized_rr=realized_rr)
            slbe = rr_calc._slbe_active(fresh_targets)
            if result == "MIXED":
                note = f"Sebagian TP sudah tercapai sebelumnya ({len(already_hit)}/{len(all_targets)} level)"
                if slbe:
                    note += " — sisa posisi closed di Entry (SLBE), bukan rugi penuh"
            else:
                note = "Belum ada TP tercapai"
            await bot.send_message(
                chat_id=channel_id,
                text=formatting.closed_sl(sig, note, result),
                parse_mode="HTML",
            )
        else:
            database.update_last_price(sig["id"], curr)


async def monitor_job(context):
    """Callback untuk python-telegram-bot JobQueue (run_repeating)."""
    await check_positions(context.bot, config.TELEGRAM_CHANNEL_ID)
