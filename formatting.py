"""Template tampilan pesan Telegram.

Semua pesan yang dikirim bot (signal baru, entry hit, TP hit, close,
status, dsb) dibentuk lewat modul ini supaya gaya visualnya konsisten
di satu tempat. Format yang dipakai: Telegram HTML (parse_mode="HTML"),
jadi setiap pengirim pesan (main.py, monitor.py, recap.py) WAJIB
menyertakan parse_mode="HTML" saat memanggil send_message/reply_text.

Kenapa HTML dan bukan Markdown? Karena karakter seperti `_` dan `*` yang
sering muncul di pair/angka (mis. underscore di nama token) gampang
merusak MarkdownV2 kalau lupa di-escape, sedangkan HTML cuma perlu
escape `< > &` yang jarang muncul di teks signal.
"""
import html

DIVIDER = "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"


def esc(value) -> str:
    """Escape teks buat aman dimasukkan ke HTML Telegram."""
    return html.escape(str(value))


def fmt_num(value) -> str:
    """Format angka tanpa nol berlebih (mis. 0.632500 -> 0.6325)."""
    return f"{value:g}"


def direction_badge(direction: str) -> str:
    return "🟢 LONG ▲" if direction == "LONG" else "🔴 SHORT ▼"


def pair_title(pair: str, direction: str) -> str:
    return f"{direction_badge(direction)}  <b>{esc(pair)}</b>"


# ---------------------------------------------------------------------------
# Signal baru terdeteksi (reply ke post channel)
# ---------------------------------------------------------------------------

def new_signal(parsed) -> str:
    tp_lines = "\n".join(
        f"   TP{t.level} · RR 1:{fmt_num(t.rr)} → <code>{fmt_num(t.price)}</code>"
        for t in parsed.targets
    )
    return (
        f"📥 <b>SIGNAL BARU TERCATAT</b>\n"
        f"{DIVIDER}\n"
        f"{pair_title(parsed.pair, parsed.direction)}\n\n"
        f"Entry      <code>{fmt_num(parsed.entry)}</code>\n"
        f"Stoploss   <code>{fmt_num(parsed.stoploss)}</code>\n\n"
        f"🎯 <b>Take Profit</b>\n"
        f"{tp_lines}\n\n"
        f"<i>Status: menunggu harga entry tersentuh...</i>"
    )


def duplicate_signal(parsed, dup: dict) -> str:
    return (
        f"⚠️ <b>SIGNAL DIABAIKAN — DUPLIKAT</b>\n"
        f"{DIVIDER}\n"
        f"{pair_title(parsed.pair, parsed.direction)}\n\n"
        f"Signal identik sudah tercatat & masih <b>{esc(dup['status'])}</b>.\n"
        f"Entry <code>{fmt_num(parsed.entry)}</code> · "
        f"SL <code>{fmt_num(parsed.stoploss)}</code>\n\n"
        f"<i>Diabaikan supaya tidak tercatat dobel.</i>"
    )


# ---------------------------------------------------------------------------
# Monitor: entry hit / TP hit / close
# ---------------------------------------------------------------------------

def entry_hit(sig: dict, curr: float) -> str:
    return (
        f"🎯 <b>ENTRY HIT</b>\n"
        f"{DIVIDER}\n"
        f"{pair_title(sig['pair'], sig['direction'])}\n\n"
        f"Entry masuk di   <code>{fmt_num(sig['entry'])}</code>\n"
        f"Harga sekarang   <code>{fmt_num(curr)}</code>\n\n"
        f"<i>Status: ACTIVE — memantau TP & SL...</i>"
    )


def tp_hit(sig: dict, target: dict, curr: float) -> str:
    return (
        f"✅ <b>TP{target['level']} TERCAPAI</b>\n"
        f"{DIVIDER}\n"
        f"{pair_title(sig['pair'], sig['direction'])}\n\n"
        f"RR 1:{fmt_num(target['rr'])} tercapai @ <code>{fmt_num(curr)}</code>"
    )


def closed_win(sig: dict, rr_label: str) -> str:
    return (
        f"🏁 <b>POSISI CLOSED — WIN</b> 🟩\n"
        f"{DIVIDER}\n"
        f"{pair_title(sig['pair'], sig['direction'])}\n\n"
        f"Semua target TP tercapai{(' (' + rr_label + ')') if rr_label else ''} 🎉"
    )


def closed_sl(sig: dict, note: str, result: str) -> str:
    tag = "MIXED — SEBAGIAN TP KENA" if result == "MIXED" else "LOSS"
    icon = "🟨" if result == "MIXED" else "🟥"
    return (
        f"🛑 <b>STOPLOSS HIT</b> — {tag} {icon}\n"
        f"{DIVIDER}\n"
        f"{pair_title(sig['pair'], sig['direction'])}\n\n"
        f"Entry <code>{fmt_num(sig['entry'])}</code> → "
        f"SL <code>{fmt_num(sig['stoploss'])}</code>\n"
        f"<i>{esc(note)}</i>"
    )


# ---------------------------------------------------------------------------
# Command manual: /status, /cancel, /close
# ---------------------------------------------------------------------------

def status_empty() -> str:
    return "📋 <b>Tidak ada posisi yang sedang dipantau saat ini.</b>"


# Batas aman di bawah limit keras Telegram (4096 char per pesan), supaya
# masih ada ruang buat header halaman tanpa perlu hitung pas-pasan.
_STATUS_MAX_CHARS = 3500


def _status_block(s: dict, targets_by_signal: dict) -> str:
    targets = sorted(targets_by_signal.get(s["id"], []), key=lambda t: t["level"])
    status_icon = "🟢 ACTIVE " if s["status"] == "ACTIVE" else "🟡 PENDING"
    tp_summary = " · ".join(
        f"TP{t['level']}{' ✅' if t['status'] == 'HIT' else ''} {fmt_num(t['price'])}"
        for t in targets
    ) or "-"
    return (
        f"{status_icon}  {pair_title(s['pair'], s['direction'])}\n"
        f"   Entry <code>{fmt_num(s['entry'])}</code> · "
        f"SL <code>{fmt_num(s['stoploss'])}</code>\n"
        f"   {tp_summary}"
    )


def status_chunks(open_signals: list[dict], targets_by_signal: dict) -> list[str]:
    """Pecah daftar posisi jadi beberapa pesan kalau kepanjangan (limit
    Telegram 4096 char/pesan). Tiap pesan dapat header halaman
    "(Halaman X/Y)" hanya kalau memang lebih dari satu halaman."""
    total = len(open_signals)
    blocks = [_status_block(s, targets_by_signal) for s in open_signals]

    pages: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for block in blocks:
        block_len = len(block) + 2  # +2 untuk "\n\n" pemisah antar blok
        if current and current_len + block_len > _STATUS_MAX_CHARS:
            pages.append(current)
            current = []
            current_len = 0
        current.append(block)
        current_len += block_len
    if current:
        pages.append(current)
    if not pages:
        pages = [[]]

    total_pages = len(pages)
    messages = []
    for i, page_blocks in enumerate(pages, start=1):
        header = f"📋 <b>POSISI DIPANTAU</b> ({total})"
        if total_pages > 1:
            header += f"  <i>(Halaman {i}/{total_pages})</i>"
        messages.append(header + "\n" + DIVIDER + "\n\n" + "\n\n".join(page_blocks))

    return messages


def cancel_missing_active() -> str:
    return None  # placeholder kalau diperlukan nanti


def cancelled(display_pair: str, pending: list[dict]) -> str:
    names = "\n".join(
        f"• {esc(s['pair'])} ({s['direction']}) entry <code>{fmt_num(s['entry'])}</code>"
        for s in pending
    )
    return (
        f"🚫 <b>POSISI PENDING DIBATALKAN</b>\n"
        f"{DIVIDER}\n"
        f"{names}"
    )


def closed_manual(closed: list[dict]) -> str:
    lines = [f"🔧 <b>POSISI DITUTUP MANUAL</b>", DIVIDER]
    for s in closed:
        sign = "🟩" if s["rr"] >= 0 else "🟥"
        lines.append(
            f"{sign} {pair_title(s['pair'], s['direction'])} "
            f"@ <code>{fmt_num(s['price'])}</code> — <b>{s['rr']:+.2f}R</b>"
        )
    return "\n".join(lines)
