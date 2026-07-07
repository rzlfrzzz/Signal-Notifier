"""Generate & kirim rekap harian / bulanan ke channel.

Asumsi position sizing: modal dibagi rata ke tiap level TP (mis. kalau ada
3 level TP, tiap level mewakili 1/3 posisi). Realized RR per signal:

- result == WIN   -> semua level TP tercapai -> RR = rata-rata RR semua level
- result == LOSS  -> SL kena, belum ada TP tercapai -> RR = -1 (rugi 1R penuh)
- result == MIXED -> SL kena setelah sebagian TP tercapai -> RR = jumlah
  (rr_level jika HIT, atau -1 jika belum) dibagi jumlah level

Selain rekap hasil closed, rekap HARIAN juga menampilkan snapshot LIVE
posisi yang sedang berjalan saat ini (PENDING/ACTIVE), di samping hasil
yang closed pada periode itu (TP/Partial/SL/Manual). Tiap pair di-hyperlink
ke pesan signal aslinya di channel.
"""
import html
from datetime import datetime, timedelta

import pytz

import config
import database
import rr_calc

TZ = pytz.timezone(config.TIMEZONE)


def _message_link(signal: dict) -> str | None:
    """Bangun link ke pesan signal asli di channel, supaya pair di rekap
    bisa di-tap langsung ke post aslinya.

    - Channel PRIVATE (chat_id numeric, mis. -1001234567890):
      https://t.me/c/1234567890/<message_id>
    - Channel PUBLIC (config.TELEGRAM_CHANNEL_ID = '@username'):
      https://t.me/username/<message_id>

    Return None kalau chat_id/message_id tidak ada atau formatnya tidak
    dikenali (link akan di-skip, pair tampil sebagai teks biasa)."""
    chat_id = signal.get("chat_id")
    message_id = signal.get("message_id")
    if not chat_id or not message_id:
        return None

    chat_id_str = str(chat_id)
    if chat_id_str.startswith("-100"):
        internal_id = chat_id_str[4:]
        return f"https://t.me/c/{internal_id}/{message_id}"

    channel = config.TELEGRAM_CHANNEL_ID
    if isinstance(channel, str) and channel.startswith("@"):
        return f"https://t.me/{channel[1:]}/{message_id}"

    return None


def _pair_label(signal: dict) -> str:
    """Nama pair singkat buat tampilan (mis. 'RE/USDT' -> 'RE')."""
    pair = signal.get("pair", "")
    return pair.split("/")[0] if pair else pair


def _pair_html(signal: dict) -> str:
    """Pair (di-escape) sebagai hyperlink HTML ke pesan aslinya kalau ada,
    fallback ke teks biasa kalau link tidak bisa dibentuk."""
    label = html.escape(_pair_label(signal))
    link = _message_link(signal)
    if link:
        return f'<a href="{link}">{label}</a>'
    return label


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
        lines.append(f"{icon} {_pair_html(s)} ({s['direction']}) — {rr:+.2f}R ({levels_text})")

    return "\n".join(lines)


def _running_pct(signal: dict) -> float | None:
    """Persentase perubahan harga dari entry, searah profit (+) / rugi (-)
    sesuai direction. None kalau belum ada last_price."""
    last_price = signal.get("last_price")
    entry = signal.get("entry")
    if last_price is None or not entry:
        return None
    change = (last_price - entry) / entry * 100
    if signal["direction"] == "SHORT":
        change = -change
    return change


def _format_live_status(
    open_signals: list[dict],
    closed_signals_with_targets: list[tuple[dict, list[dict]]],
) -> str:
    """Section snapshot: hitung Active/Waiting/Closed lalu list per kategori.

    - Waiting  : posisi PENDING saat ini (live, tidak dibatasi periode)
    - Running  : posisi ACTIVE saat ini (live), dengan % floating dari entry
    - TP       : signal WIN yang closed pada periode rekap ini
    - Partial  : signal MIXED (SL kena setelah sebagian TP) pada periode ini
    - SL       : signal LOSS murni pada periode ini
    - Manual   : signal MANUAL (/close) pada periode ini
    """
    waiting = [s for s in open_signals if s["status"] == "PENDING"]
    running = [s for s in open_signals if s["status"] == "ACTIVE"]

    wins = [s for s, _ in closed_signals_with_targets if s["result"] == "WIN"]
    mixed = [s for s, _ in closed_signals_with_targets if s["result"] == "MIXED"]
    losses = [s for s, _ in closed_signals_with_targets if s["result"] == "LOSS"]
    manual = [s for s, _ in closed_signals_with_targets if s["result"] == "MANUAL"]

    lines = [
        f"🟢 Active : {len(running)}",
        f"🟡 Waiting : {len(waiting)}",
        f"🔴 Closed : {len(closed_signals_with_targets)}",
        "",
        "━━━━━━━━━━━━━━",
    ]

    if waiting:
        lines.append("")
        lines.append("⏳ Waiting")
        for s in waiting:
            lines.append(f"• {_pair_html(s)}")

    if running:
        lines.append("")
        lines.append("🚀 Running")
        for s in running:
            pct = _running_pct(s)
            pct_text = f" ({pct:+.1f}%)" if pct is not None else ""
            lines.append(f"• {_pair_html(s)}{pct_text}")

    if wins:
        lines.append("")
        lines.append("💰 TP")
        for s in wins:
            lines.append(f"• {_pair_html(s)} ✅")

    if mixed:
        lines.append("")
        lines.append("🟡 Partial (SL setelah sebagian TP)")
        for s in mixed:
            lines.append(f"• {_pair_html(s)}")

    if losses:
        lines.append("")
        lines.append("❌ SL")
        for s in losses:
            lines.append(f"• {_pair_html(s)}")

    if manual:
        lines.append("")
        lines.append("🔧 Manual")
        for s in manual:
            lines.append(f"• {_pair_html(s)}")

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
    signals_with_targets = _with_targets(signals)
    open_signals = database.get_open_signals()

    title = f"📊 REKAP HARIAN — {start_local.strftime('%d %B %Y')}"
    status_section = _format_live_status(open_signals, signals_with_targets)
    result_section = _format_recap("📈 Hasil Closed Hari Ini", signals_with_targets)

    text = f"{title}\n\n{status_section}\n\n━━━━━━━━━━━━━━\n\n{result_section}"
    await bot.send_message(
        chat_id=config.TELEGRAM_CHANNEL_ID,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


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
    await bot.send_message(
        chat_id=config.TELEGRAM_CHANNEL_ID,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def daily_recap_job(context):
    await send_daily_recap(context.bot)


async def monthly_recap_job(context):
    await send_monthly_recap(context.bot)
