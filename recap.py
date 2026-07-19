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
import re
from collections import Counter
from datetime import datetime, timedelta

import pytz

import config
import database
import formatting
import rr_calc

TZ = pytz.timezone(config.TIMEZONE)
DIVIDER = formatting.DIVIDER


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


def _realized_pct(signal: dict, targets: list[dict]) -> float:
    """Persentase gain/loss realized, dihitung dari harga (entry vs
    target/SL/close price) — bukan dari RR, supaya bisa diakumulasi
    apple-to-apple antar signal (lihat catatan di rr_calc.py)."""
    direction = signal["direction"]
    entry = signal["entry"]
    if signal["result"] == "MANUAL":
        close_price = signal.get("last_price")
        if close_price is None:
            return 0.0
        return rr_calc.compute_manual_pct(direction, entry, close_price)
    return rr_calc.compute_realized_pct(
        signal["result"], direction, entry, signal["stoploss"], targets
    )


def _manual_is_win(signal: dict, targets: list[dict]) -> bool:
    """Signal MANUAL (ditutup via /close) dianggap WIN kalau realized_pct-nya
    >= 0 (profit/breakeven), dan LOSS kalau minus. Dipakai supaya Win Rate
    tetap mencerminkan hasil sebenarnya dari close manual, bukan otomatis
    dianggap bukan-win."""
    return _realized_pct(signal, targets) >= 0


def _bar(pct: float, size: int = 10) -> str:
    filled = round((pct / 100) * size)
    filled = max(0, min(size, filled))
    return "█" * filled + "░" * (size - filled)


def _recap_detail_line(s: dict, t: list[dict]) -> str:
    """Satu baris detail untuk satu signal yang closed."""
    if s["result"] == "MANUAL":
        icon = "🔧✅" if _manual_is_win(s, t) else "🔧🛑"
    else:
        icon = {"WIN": "✅", "LOSS": "🛑", "MIXED": "🟡"}.get(s["result"], "•")
    rr = _realized_rr(s, t)
    pct = _realized_pct(s, t)
    hit_levels = [tt["level"] for tt in t if tt["status"] == "HIT"]
    if s["result"] == "MANUAL":
        levels_text = "closed manual"
    else:
        levels_text = f"TP{','.join(str(l) for l in hit_levels)} hit" if hit_levels else "no TP hit"
    return (
        f"{icon} {_pair_html(s)} ({s['direction']}) — "
        f"<b>{pct:+.2f}%</b>  <i>({rr:+.2f}R · {levels_text})</i>"
    )


# Berapa baris detail signal digabung jadi satu "block" pagination. Angka
# kecil supaya kalau ada banyak sekali signal closed dalam sehari, tetap
# bisa dipecah antar block alih-alih jadi satu block raksasa yang sendirian
# sudah melebihi _STATUS_MAX_CHARS.
_DETAIL_LINES_PER_BLOCK = 15


def _mixed_tp_label(targets: list[dict]) -> str:
    """Label ringkas level TP yang tercapai sebelum SL kena, mis. 'TP1 only'
    untuk satu level, atau 'TP1-TP2' untuk beberapa level berurutan."""
    hit_levels = sorted(t["level"] for t in targets if t["status"] == "HIT")
    if not hit_levels:
        return "No TP hit"
    if len(hit_levels) == 1:
        return f"TP{hit_levels[0]} only"
    return f"TP{hit_levels[0]}-TP{hit_levels[-1]}"


def _mixed_breakdown(mixed_with_targets: list[tuple[dict, list[dict]]]) -> str:
    """Breakdown signal MIXED berdasarkan level TP yang tercapai sebelum SL,
    mis. 'TP1 only : 7, TP1-TP2 : 4'. Jauh lebih informatif buat evaluasi
    strategi partial-TP dibanding cuma menampilkan total jumlah Mixed."""
    if not mixed_with_targets:
        return ""
    counter = Counter(_mixed_tp_label(t) for _, t in mixed_with_targets)

    def _sort_key(item):
        nums = re.findall(r"\d+", item[0])
        return int(nums[0]) if nums else 0

    ordered = sorted(counter.items(), key=_sort_key)
    return ", ".join(f"{label} : {count}" for label, count in ordered)


def _fmt_ratio(value: float) -> str:
    """Format rasio (Profit Factor / Avg Reward-Risk) dengan penanganan
    kasus pembagi nol (tidak ada loss sama sekali -> tak terhingga)."""
    if value == float("inf"):
        return "∞"
    return f"{value:.2f}"


def _performance_metrics(signals_with_targets: list[tuple[dict, list[dict]]]) -> dict:
    """Metrik performa profesional (Net Result, Profit Factor, Avg Win/Loss,
    Avg Reward/Risk), dihitung dari realized R-multiple SEMUA signal closed.

    PENTING: klasifikasi profit/loss di sini pakai TANDA realized RR
    (rr > 0 = profit trade, rr < 0 = loss trade) - BUKAN label outcome
    (WIN/MIXED/LOSS/MANUAL). Ini standar profesional karena signal MIXED
    atau MANUAL tetap bisa net profit meski bukan 'WIN' murni (begitu juga
    sebaliknya), jadi Profit Factor/Avg Win/Avg Loss harus mencerminkan
    hasil R aktual, bukan kategori penutupannya.

    R-multiple dipakai (bukan %) karena tiap signal punya jarak SL beda-beda
    -> 1R selalu merepresentasikan risiko yang sama secara proporsional,
    jadi bisa dijumlah/dirata-rata apple-to-apple antar signal. Persentase
    cuma dipakai sebagai info tambahan (lihat _realized_pct), bukan basis
    Net Result/Profit Factor.
    """
    rr_values = [_realized_rr(s, t) for s, t in signals_with_targets]
    profit_rrs = [rr for rr in rr_values if rr > 0]
    loss_rrs = [rr for rr in rr_values if rr < 0]

    gross_profit = sum(profit_rrs)
    gross_loss = abs(sum(loss_rrs))

    avg_win = (gross_profit / len(profit_rrs)) if profit_rrs else 0.0
    avg_loss = -(gross_loss / len(loss_rrs)) if loss_rrs else 0.0

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0

    if avg_loss != 0:
        avg_rr_ratio = abs(avg_win / avg_loss)
    else:
        avg_rr_ratio = float("inf") if avg_win > 0 else 0.0

    return {
        "net_rr": sum(rr_values),
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_rr_ratio": avg_rr_ratio,
    }


def _stats_block(title: str, signals_with_targets: list[tuple[dict, list[dict]]]) -> str:
    """Bangun satu block teks statistik (tanpa detail per-signal) untuk
    sebuah periode. Dipakai baik untuk rekap penuh (_format_recap_blocks)
    maupun ringkasan singkat (mis. bulan lalu di rekap bulanan manual).

    Urutan tampilan diprioritaskan dari metrik paling penting ke paling
    kurang penting buat evaluasi strategi: (1) Net Result R, (2) Profit
    Factor, (3) Win Rate, (4) Avg Win/Loss & Avg Reward-Risk, (5) breakdown
    jumlah outcome, (6) persentase (cuma info tambahan). Manual Closed
    ditampilkan terpisah sebagai metadata metode-exit, BUKAN sebagai
    kategori outcome (karena hasil aktualnya sudah tercermin di Win/Loss
    lewat reklasifikasi win/loss manual)."""
    total = len(signals_with_targets)
    if total == 0:
        return f"{title}\n{DIVIDER}\n\n<i>Belum ada signal yang closed pada periode ini.</i>"

    wins = [s for s, _ in signals_with_targets if s["result"] == "WIN"]
    losses = [s for s, _ in signals_with_targets if s["result"] == "LOSS"]
    mixed_wt = [(s, t) for s, t in signals_with_targets if s["result"] == "MIXED"]
    manual_wt = [(s, t) for s, t in signals_with_targets if s["result"] == "MANUAL"]

    manual_wins = [s for s, t in manual_wt if _manual_is_win(s, t)]
    manual_losses = [s for s, t in manual_wt if not _manual_is_win(s, t)]

    # Win Rate secara eksplisit disebut "termasuk manual win" supaya
    # transparan bahwa manual close yang net profit ikut dihitung di
    # pembilang - bukan cuma outcome WIN otomatis.
    win_rate = ((len(wins) + len(manual_wins)) / total) * 100
    total_pct = sum(_realized_pct(s, t) for s, t in signals_with_targets)

    perf = _performance_metrics(signals_with_targets)
    net_icon = "🟩" if perf["net_rr"] >= 0 else "🟥"
    mixed_breakdown_str = _mixed_breakdown(mixed_wt)

    stats_lines = [title, DIVIDER, ""]

    # 1) Net Result — metrik utama (R-multiple, % cuma info tambahan)
    stats_lines.append(
        f"Net Result      {net_icon} <b>{perf['net_rr']:+.2f}R</b>  <i>({total_pct:+.2f}%)</i>"
    )
    # 2) Profit Factor — kesehatan strategi
    stats_lines.append(f"Profit Factor   <b>{_fmt_ratio(perf['profit_factor'])}</b>")
    # 3) Win Rate
    stats_lines.append(
        f"Win Rate        {_bar(win_rate)}  <b>{win_rate:.1f}%</b>  "
        f"<i>(termasuk manual win)</i>"
    )
    stats_lines.append("")
    # 4) Karakteristik strategi: Avg Win/Loss & Avg Reward-Risk
    stats_lines.append(f"Avg Win         <b>{perf['avg_win']:+.2f}R</b>")
    stats_lines.append(f"Avg Loss        <b>{perf['avg_loss']:+.2f}R</b>")
    stats_lines.append(f"Avg Reward/Risk <b>{_fmt_ratio(perf['avg_rr_ratio'])}</b>")
    stats_lines.append("")
    # 5) Breakdown jumlah outcome (Manual TIDAK termasuk kategori outcome)
    stats_lines.append(f"Total Signal    <b>{total}</b>")
    stats_lines.append("<b>Outcome</b>")
    stats_lines.append(f"✅ Win      : <b>{len(wins)}</b>")
    mixed_suffix = f"  <i>({mixed_breakdown_str})</i>" if mixed_breakdown_str else ""
    stats_lines.append(f"🟡 Mixed    : <b>{len(mixed_wt)}</b>{mixed_suffix}")
    stats_lines.append(f"🛑 Loss     : <b>{len(losses)}</b>")
    stats_lines.append("")
    # Manual close = metadata metode-exit, direklasifikasi jadi W/L aktual,
    # ditampilkan terpisah dari Outcome supaya tidak dobel-hitung.
    stats_lines.append(
        f"Manual Closed   : <b>{len(manual_wt)}</b> "
        f"<i>({len(manual_wins)}W / {len(manual_losses)}L)</i>"
    )

    return "\n".join(stats_lines)


def _format_recap_blocks(
    title: str, signals_with_targets: list[tuple[dict, list[dict]]]
) -> list[str]:
    """Pecah rekap (statistik + detail per-signal) jadi beberapa block teks,
    supaya bisa dipaginasi oleh `_paginate_blocks` tanpa melebihi limit
    karakter pesan Telegram."""
    blocks = [_stats_block(title, signals_with_targets)]
    if not signals_with_targets:
        return blocks

    detail_lines = [_recap_detail_line(s, t) for s, t in signals_with_targets]
    for i in range(0, len(detail_lines), _DETAIL_LINES_PER_BLOCK):
        chunk_lines = detail_lines[i:i + _DETAIL_LINES_PER_BLOCK]
        header = f"{DIVIDER}\n<b>Detail Signal</b>" if i == 0 else "<b>Detail Signal (lanjutan)</b>"
        blocks.append(header + "\n" + "\n".join(chunk_lines))

    return blocks


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


def _running_line(s: dict, targets: list[dict]) -> str:
    """Satu baris posisi Running: pair, % floating dari entry, dan level TP
    tertinggi yang sudah kesentuh (kalau ada)."""
    pct = _running_pct(s)
    pct_text = f"  <b>{pct:+.1f}%</b>" if pct is not None else ""
    hit_levels = sorted(t["level"] for t in targets if t["status"] == "HIT")
    tp_text = f"  ✅TP{hit_levels[-1]}" if hit_levels else ""
    return f"   • {_pair_html(s)}{pct_text}{tp_text}"


def _format_live_status_blocks(
    open_signals: list[dict],
    closed_signals_with_targets: list[tuple[dict, list[dict]]],
    open_targets_by_signal: dict | None = None,
) -> list[str]:
    """Section snapshot: hitung Active/Waiting/Closed lalu list per kategori,
    dikembalikan sebagai list of blocks supaya bisa dipaginasi.

    - Waiting  : posisi PENDING saat ini (live, tidak dibatasi periode)
    - Running  : posisi ACTIVE saat ini (live), dengan % floating dari entry
                 dan TP tertinggi yang sudah kesentuh (kalau ada)
    - TP       : signal WIN yang closed pada periode rekap ini
    - Partial  : signal MIXED (SL kena setelah sebagian TP) pada periode ini
    - SL       : signal LOSS murni pada periode ini
    - Manual   : signal MANUAL (/close) pada periode ini
    """
    open_targets_by_signal = open_targets_by_signal or {}
    waiting = [s for s in open_signals if s["status"] == "PENDING"]
    running = [s for s in open_signals if s["status"] == "ACTIVE"]

    wins = [s for s, _ in closed_signals_with_targets if s["result"] == "WIN"]
    mixed = [s for s, _ in closed_signals_with_targets if s["result"] == "MIXED"]
    losses = [s for s, _ in closed_signals_with_targets if s["result"] == "LOSS"]
    manual = [s for s, _ in closed_signals_with_targets if s["result"] == "MANUAL"]

    blocks = ["<b>📌 Snapshot Posisi</b>\n"
              f"🟢 Active : <b>{len(running)}</b>   "
              f"🟡 Waiting : <b>{len(waiting)}</b>   "
              f"🔴 Closed : <b>{len(closed_signals_with_targets)}</b>"]

    if waiting:
        blocks.append(
            "⏳ <b>Waiting</b>\n" + "\n".join(f"   • {_pair_html(s)}" for s in waiting)
        )

    if running:
        blocks.append(
            "🚀 <b>Running</b>\n" + "\n".join(
                _running_line(s, open_targets_by_signal.get(s["id"], [])) for s in running
            )
        )

    if wins:
        blocks.append(
            "💰 <b>TP</b>\n" + "\n".join(f"   • {_pair_html(s)} ✅" for s in wins)
        )

    if mixed:
        blocks.append(
            "🟡 <b>Partial</b>  <i>(SL setelah sebagian TP)</i>\n"
            + "\n".join(f"   • {_pair_html(s)}" for s in mixed)
        )

    if losses:
        blocks.append(
            "❌ <b>SL</b>\n" + "\n".join(f"   • {_pair_html(s)}" for s in losses)
        )

    if manual:
        blocks.append(
            "🔧 <b>Manual</b>\n" + "\n".join(f"   • {_pair_html(s)}" for s in manual)
        )

    return blocks


def _with_targets(signals: list[dict]) -> list[tuple[dict, list[dict]]]:
    result = []
    for s in signals:
        targets = database.get_all_targets_for_signal(s["id"])
        result.append((s, targets))
    return result


# Batas aman di bawah limit keras Telegram (4096 char per pesan).
_STATUS_MAX_CHARS = 3500


def _paginate_blocks(
    header: str, blocks: list[str], max_chars: int = _STATUS_MAX_CHARS
) -> list[str]:
    """Susun `header` + daftar `blocks` jadi satu atau lebih pesan, masing-
    masing di bawah `max_chars`. Kalau perlu lebih dari satu pesan, tiap
    pesan diberi label "(Halaman X/Y)" di header-nya.

    Dipakai bersama oleh /status dan rekap harian/bulanan supaya keduanya
    tidak pernah mengirim pesan yang melebihi limit 4096 karakter Telegram
    (dulu rekap harian/bulanan mengirim satu pesan mentah tanpa dipecah,
    dan bisa gagal terkirim kalau signal closed dalam periode itu banyak).

    Catatan: kalau satu block SENDIRIAN sudah melebihi max_chars (mis. ada
    ratusan posisi Running sekaligus), block itu tetap dikirim apa adanya
    di halamannya sendiri (best effort) alih-alih dipotong di tengah HTML,
    supaya tag HTML tidak rusak."""
    if not blocks:
        return [header]

    pages: list[list[str]] = []
    current: list[str] = []
    current_len = len(header) + 2
    for block in blocks:
        block_len = len(block) + 2
        if current and current_len + block_len > max_chars:
            pages.append(current)
            current = []
            current_len = len(header) + 2
        current.append(block)
        current_len += block_len
    if current:
        pages.append(current)

    total_pages = len(pages)
    messages = []
    for i, page_blocks in enumerate(pages, start=1):
        h = header
        if total_pages > 1:
            h += f"  <i>(Halaman {i}/{total_pages})</i>"
        messages.append(h + "\n\n" + "\n\n".join(page_blocks))
    return messages


def status_snapshot_chunks(open_signals: list[dict], targets_by_signal: dict) -> list[str]:
    """Snapshot ala rekap harian tapi cuma Waiting & Running — dipakai buat
    command /status. Dipecah jadi beberapa pesan kalau kepanjangan."""
    waiting = [s for s in open_signals if s["status"] == "PENDING"]
    running = [s for s in open_signals if s["status"] == "ACTIVE"]

    header = (
        "<b>📌 Snapshot Posisi</b>\n"
        f"🟢 Active : <b>{len(running)}</b>   🟡 Waiting : <b>{len(waiting)}</b>"
    )

    if not waiting and not running:
        return [header + "\n\n<i>Tidak ada posisi yang sedang dipantau saat ini.</i>"]

    blocks = []
    if waiting:
        blocks.append(
            "⏳ <b>Waiting</b>\n" + "\n".join(f"   • {_pair_html(s)}" for s in waiting)
        )
    if running:
        blocks.append(
            "🚀 <b>Running</b>\n" + "\n".join(
                _running_line(s, targets_by_signal.get(s["id"], [])) for s in running
            )
        )

    return _paginate_blocks(header, blocks)


async def send_daily_recap(bot, for_date: datetime | None = None):
    now_local = for_date or datetime.now(TZ)
    start_local = TZ.localize(datetime(now_local.year, now_local.month, now_local.day))
    end_local = start_local + timedelta(days=1)

    start_utc = start_local.astimezone(pytz.utc).isoformat()
    end_utc = end_local.astimezone(pytz.utc).isoformat()

    signals = database.get_closed_signals_between(start_utc, end_utc)
    signals_with_targets = _with_targets(signals)
    open_signals = database.get_open_signals()
    open_targets_by_signal = database.get_targets_for_signals([s["id"] for s in open_signals])

    title = f"📊 <b>REKAP HARIAN</b> — {start_local.strftime('%d %B %Y')}"
    status_blocks = _format_live_status_blocks(open_signals, signals_with_targets, open_targets_by_signal)
    result_blocks = _format_recap_blocks("📈 <b>Hasil Closed Hari Ini</b>", signals_with_targets)

    # Gabung semua block jadi satu daftar, lalu dipaginasi bareng-bareng
    # supaya tidak ada satupun pesan yang melebihi limit Telegram, meskipun
    # hari itu ada banyak posisi live + banyak signal closed sekaligus.
    all_blocks = status_blocks + [f"{DIVIDER}"] + result_blocks
    for chunk in _paginate_blocks(f"{title}\n{DIVIDER}", all_blocks):
        await bot.send_message(
            chat_id=config.TELEGRAM_CHANNEL_ID,
            text=chunk,
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
    title = f"📅 <b>REKAP BULANAN</b> — {start_local.strftime('%B %Y')}"
    # Judul singkat untuk block statistik (beda dari title utama di atas,
    # yang dipakai sebagai header pagination supaya tidak dobel/kosong
    # ketika hasilnya dipecah jadi lebih dari satu halaman).
    result_blocks = _format_recap_blocks("📈 <b>Ringkasan</b>", _with_targets(signals))

    for chunk in _paginate_blocks(f"{title}\n{DIVIDER}", result_blocks):
        await bot.send_message(
            chat_id=config.TELEGRAM_CHANNEL_ID,
            text=chunk,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


async def send_monthly_recap_manual(bot, for_date: datetime | None = None):
    """Rekap bulanan versi manual (dipicu /rekap_bulanan): tampilkan progres
    BULAN INI (month-to-date, dari tanggal 1 s/d sekarang) lengkap dengan
    detail per-signal, plus ringkasan singkat (statistik saja, tanpa detail)
    BULAN LALU sebagai pembanding.

    Beda dengan `send_monthly_recap` (dipakai job otomatis tanggal 1) yang
    selalu merekap bulan yang baru saja selesai."""
    now_local = for_date or datetime.now(TZ)

    this_month_start = TZ.localize(datetime(now_local.year, now_local.month, 1))
    this_month_start_utc = this_month_start.astimezone(pytz.utc).isoformat()
    now_utc = now_local.astimezone(pytz.utc).isoformat()

    last_month_end_local = this_month_start
    last_month_start_local = (this_month_start - timedelta(days=1)).replace(day=1)
    last_month_start_utc = last_month_start_local.astimezone(pytz.utc).isoformat()
    last_month_end_utc = last_month_end_local.astimezone(pytz.utc).isoformat()

    this_month_signals = database.get_closed_signals_between(this_month_start_utc, now_utc)
    last_month_signals = database.get_closed_signals_between(last_month_start_utc, last_month_end_utc)

    title = (
        f"📅 <b>REKAP BULANAN</b> — {now_local.strftime('%B %Y')} "
        f"<i>(berjalan s/d {now_local.strftime('%d %b')})</i>"
    )

    this_month_blocks = _format_recap_blocks(
        "📈 <b>Periode Bulan Ini</b>", _with_targets(this_month_signals)
    )
    last_month_summary_block = _stats_block(
        f"📊 <b>Ringkasan Bulan Lalu</b> — {last_month_start_local.strftime('%B %Y')}",
        _with_targets(last_month_signals),
    )

    all_blocks = this_month_blocks + [DIVIDER, last_month_summary_block]

    for chunk in _paginate_blocks(f"{title}\n{DIVIDER}", all_blocks):
        await bot.send_message(
            chat_id=config.TELEGRAM_CHANNEL_ID,
            text=chunk,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


async def daily_recap_job(context):
    await send_daily_recap(context.bot)


async def monthly_recap_job(context):
    await send_monthly_recap(context.bot)
