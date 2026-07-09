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

def new_signal(parsed, conflicts: list[dict] | None = None) -> str:
    tp_lines = "\n".join(
        f"   TP{t.level} · RR 1:{fmt_num(t.rr)} → <code>{fmt_num(t.price)}</code>"
        for t in parsed.targets
    )
    text = (
        f"📥 <b>SIGNAL BARU TERCATAT</b>\n"
        f"{DIVIDER}\n"
        f"{pair_title(parsed.pair, parsed.direction)}\n\n"
        f"Entry      <code>{fmt_num(parsed.entry)}</code>\n"
        f"Stoploss   <code>{fmt_num(parsed.stoploss)}</code>\n\n"
        f"🎯 <b>Take Profit</b>\n"
        f"{tp_lines}\n\n"
        f"<i>Status: menunggu harga entry tersentuh...</i>"
    )

    if conflicts:
        conflict_lines = "\n".join(
            f"   • {direction_badge(c['direction'])} · Entry <code>{fmt_num(c['entry'])}</code> "
            f"· SL <code>{fmt_num(c['stoploss'])}</code> · <b>{esc(c['status'])}</b>"
            for c in conflicts
        )
        text += (
            f"\n\n{DIVIDER}\n"
            f"⚠️ <b>PERHATIAN — sudah ada signal lain yang masih berjalan di "
            f"{esc(parsed.pair)}:</b>\n"
            f"{conflict_lines}\n"
            f"<i>Cek lagi supaya tidak bentrok / dobel posisi di pair yang sama.</i>"
        )

    return text


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
# Command manual: /cancel, /close
# (/status sekarang pakai recap.status_snapshot_chunks, format snapshot
# yang sama dengan section live di rekap harian)
# ---------------------------------------------------------------------------




def invalidated(sig: dict, last_target: dict, curr: float) -> str:
    return (
        f"❌ <b>SIGNAL TIDAK VALID</b>\n"
        f"{DIVIDER}\n"
        f"{pair_title(sig['pair'], sig['direction'])}\n\n"
        f"Entry <code>{fmt_num(sig['entry'])}</code> belum sempat kesentuh, "
        f"tapi harga sudah tembus TP{last_target['level']} "
        f"(<code>{fmt_num(last_target['price'])}</code>) di <code>{fmt_num(curr)}</code>.\n\n"
        f"<i>Signal dibatalkan otomatis — harga sudah bergerak terlalu jauh "
        f"dari area entry.</i>"
    )


def cancel_channel_notice(pending: list[dict]) -> str:
    names = "\n".join(
        f"• {pair_title(s['pair'], s['direction'])} — entry <code>{fmt_num(s['entry'])}</code>"
        for s in pending
    )
    return (
        f"🚫 <b>SIGNAL DIBATALKAN</b>\n"
        f"{DIVIDER}\n"
        f"{names}\n\n"
        f"<i>Dibatalkan manual oleh admin.</i>"
    )


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
