"""Generate & kirim rekap harian / bulanan ke channel.

Asumsi position sizing: modal dibagi rata ke tiap level TP (mis. kalau ada
3 level TP, tiap level mewakili 1/3 posisi). Realized RR per signal:

- result == WIN   -> semua level TP tercapai -> RR = rata-rata RR semua level
- result == LOSS  -> SL kena, belum ada TP tercapai -> RR = -1 (rugi 1R penuh)
- result == MIXED -> SL kena setelah sebagian TP tercapai -> RR = jumlah
  (rr_level jika HIT, atau -1 jika belum) dibagi jumlah level
"""
from datetime import datetime, timedelta
import pytz

import config
import database
import rr_calc

TZ = pytz.timezone(config.TIMEZONE)


def _realized_rr(signal: dict, targets: list[dict]) -> float:
    """Pakai realized_rr yang sudah tersimpan di kolom signals (diisi saat
    close, baik otomatis maupun manual). Fallback hitung ulang dari target
    untuk data lama yang belum punya kolom ini terisi."""
    stored = signal.get("realized_rr")
    if stored is not None:
        return float(stored)
    return rr_calc.compute_realized_rr(signal["result"], targets)


def _format_recap(title: str, signals_with_targets: list[tuple[dict, list[dict]]]) -> str:
    total = len(signals_with_targets)
    if total == 0:
        return f"{title}\n\nBelum ada signal yang closed pada periode ini."

    wins = [s for s, _ in signals_with_targets if s["result"] == "WIN"]
    losses = [s for s, _ in signals_with_targets if s["result"] == "LOSS"]
    mixed = [s for s, _ in signals_with_targets if s["result"] == "MIXED"]
    manual = [s for s, _ in signals_with_targets if s["result"] == "MANUAL"]

    win_rate = (len(wins) / total) * 100
    total_rr = sum(_realized_rr(s, t) for s, t in signals_with_targets)

    lines = [title, ""]
    lines.append(f"Total Signal : {total}")
    lines.append(f"✅ Win    : {len(wins)}")
    lines.append(f"🟡 Mixed  : {len(mixed)} (partial TP sebelum SL)")
    lines.append(f"🛑 Loss   : {len(losses)}")
    lines.append(f"🔧 Manual : {len(manual)} (ditutup manual via /close)")
    lines.append(f"Win Rate : {win_rate:.1f}%")
    lines.append(f"Total RR : {total_rr:+.2f}R")
    lines.append("")
    lines.append("Detail:")
    for s, t in signals_with_targets:
        icon = {"WIN": "✅", "LOSS": "🛑", "MIXED": "🟡", "MANUAL": "🔧"}.get(s["result"], "•")
        rr = _realized_rr(s, t)
        hit_levels = [tt["level"] for tt in t if tt["status"] == "HIT"]
        if s["result"] == "MANUAL":
            levels_text = "closed manual"
        else:
            levels_text = f"TP{','.join(str(l) for l in hit_levels)} hit" if hit_levels else "no TP hit"
        lines.append(f"{icon} {s['pair']} ({s['direction']}) — {rr:+.2f}R ({levels_text})")

    return "\n".join(lines)


def _with_targets(signals: list[dict]) -> list[tuple[dict, list[dict]]]:
    result = []
    for s in signals:
        targets = database.get_all_targets_for_signal(s["id"])
        result.append((s, targets))
    return result


async def send_daily_recap(bot, for_date: datetime | None = None):
    now_local = for_date or datetime.now(TZ)
    start_local = TZ.localize(datetime(now_local.year, now_local.month, now_local.day))
    end_local = start_local + timedelta(days=1)

    start_utc = start_local.astimezone(pytz.utc).isoformat()
    end_utc = end_local.astimezone(pytz.utc).isoformat()

    signals = database.get_closed_signals_between(start_utc, end_utc)
    title = f"📊 REKAP HARIAN — {start_local.strftime('%d %B %Y')}"
    text = _format_recap(title, _with_targets(signals))
    await bot.send_message(chat_id=config.TELEGRAM_CHANNEL_ID, text=text)


async def send_monthly_recap(bot, for_date: datetime | None = None):
    """Default: rekap BULAN SEBELUMNYA (dipanggil tanggal 1)."""
    now_local = for_date or datetime.now(TZ)
    first_of_this_month = now_local.replace(day=1)
    last_month_end = first_of_this_month
    last_month_start = (first_of_this_month - timedelta(days=1)).replace(day=1)

    start_local = TZ.localize(datetime(last_month_start.year, last_month_start.month, 1))
    end_local = TZ.localize(datetime(last_month_end.year, last_month_end.month, 1))

    start_utc = start_local.astimezone(pytz.utc).isoformat()
    end_utc = end_local.astimezone(pytz.utc).isoformat()

    signals = database.get_closed_signals_between(start_utc, end_utc)
    title = f"📅 REKAP BULANAN — {start_local.strftime('%B %Y')}"
    text = _format_recap(title, _with_targets(signals))
    await bot.send_message(chat_id=config.TELEGRAM_CHANNEL_ID, text=text)


async def daily_recap_job(context):
    await send_daily_recap(context.bot)


async def monthly_recap_job(context):
    await send_monthly_recap(context.bot)
