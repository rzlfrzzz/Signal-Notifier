"""Parser untuk format signal trading di channel Telegram.

Mendukung dua variasi format contoh:

Format A:
    🔥 $RE/USDT (LONG) 🟢
    Entry : 0.6325
    Stoploss : 0.6000
    Target : on chart

Format B:
    🟢 LONG SIGNAL ↗️
    Pair: $NOM
    Entry: 0.002236
    Stop Loss: 0.001414

Karena target biasanya "on chart" (bukan angka eksplisit), Take Profit
dihitung otomatis sebagai BEBERAPA level mengikuti RR:
    TP1 = RR 1:1, TP2 = RR 1:2, TP3 = RR 1:3, dst
sampai config.DEFAULT_RR_MAX (default 3).

Kalau signal menyebutkan RR eksplisit (mis. "RR 1:5"), angka itu dipakai
sebagai RR level tertinggi (jadi TP1..TP4 + TP5 di RR 1:5).
"""
import re
from dataclasses import dataclass
from typing import Optional

import config


@dataclass
class TargetLevel:
    level: int      # 1, 2, 3, ...
    rr: float        # RR di level ini, mis. 1, 2, 3
    price: float


@dataclass
class ParsedSignal:
    pair: str            # tampilan asli, mis. "RE/USDT"
    symbol: str           # symbol MEXC, mis. "REUSDT"
    direction: str         # "LONG" | "SHORT"
    entry: float
    stoploss: float
    targets: list[TargetLevel]   # TP1, TP2, TP3, ...


_DIRECTION_RE = re.compile(r"\b(LONG|SHORT)\b", re.IGNORECASE)
_PAIR_LABELED_RE = re.compile(r"Pair\s*:?\s*\$?([A-Za-z0-9]+)(?:\s*/\s*([A-Za-z]{2,6}))?", re.IGNORECASE)
_PAIR_DOLLAR_RE = re.compile(r"\$([A-Za-z0-9]+)(?:\s*/\s*([A-Za-z]{2,6}))?")
_ENTRY_RE = re.compile(r"Entry\s*:?\s*\$?\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_SL_RE = re.compile(r"(?:Stop\s*-?\s*Loss|Stoploss|SL)\s*:?\s*\$?\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_RR_RE = re.compile(r"RR\s*:?\s*1\s*:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)


def _normalize_symbol(ticker: str, quote: Optional[str]) -> tuple[str, str]:
    """Return (display_pair, mexc_symbol)."""
    ticker = ticker.upper().strip()
    quote = (quote or config.DEFAULT_QUOTE).upper().strip()
    display_pair = f"{ticker}/{quote}"
    symbol = f"{ticker}{quote}"
    return display_pair, symbol


def _build_targets(direction: str, entry: float, stoploss: float, rr_max: float) -> list[TargetLevel]:
    """Generate TP1 (RR 1:1), TP2 (RR 1:2), ... sampai rr_max.

    Kalau rr_max bukan bilangan bulat (mis. 4.5), level terakhir dibulatkan
    turun ke integer lalu ditambah satu level ekstra persis di rr_max.
    Contoh rr_max=3   -> RR levels: [1, 2, 3]
    Contoh rr_max=4.5 -> RR levels: [1, 2, 3, 4, 4.5]
    """
    risk = abs(entry - stoploss)
    whole = int(rr_max)
    rr_levels = [float(i) for i in range(1, max(whole, 1) + 1)]
    if rr_max > whole:
        rr_levels.append(rr_max)

    targets = []
    for i, rr in enumerate(rr_levels, start=1):
        if direction == "LONG":
            price = entry + rr * risk
        else:
            price = entry - rr * risk
        targets.append(TargetLevel(level=i, rr=rr, price=price))
    return targets


def parse_signal(text: str) -> Optional[ParsedSignal]:
    """Coba parse pesan channel sebagai signal trading.

    Return None kalau pesan bukan signal (field wajib tidak ditemukan),
    supaya pesan lain di channel (pengumuman, dsb) tidak ikut ke-parse.
    """
    if not text:
        return None

    direction_match = _DIRECTION_RE.search(text)
    entry_match = _ENTRY_RE.search(text)
    sl_match = _SL_RE.search(text)

    if not (direction_match and entry_match and sl_match):
        return None

    pair_match = _PAIR_LABELED_RE.search(text) or _PAIR_DOLLAR_RE.search(text)
    if not pair_match:
        return None

    direction = direction_match.group(1).upper()
    ticker = pair_match.group(1)
    quote = pair_match.group(2)
    display_pair, symbol = _normalize_symbol(ticker, quote)

    try:
        entry = float(entry_match.group(1))
        stoploss = float(sl_match.group(1))
    except ValueError:
        return None

    risk = abs(entry - stoploss)
    if risk == 0:
        return None

    rr_match = _RR_RE.search(text)
    rr_max = float(rr_match.group(1)) if rr_match else config.DEFAULT_RR_MAX

    targets = _build_targets(direction, entry, stoploss, rr_max)

    return ParsedSignal(
        pair=display_pair,
        symbol=symbol,
        direction=direction,
        entry=entry,
        stoploss=stoploss,
        targets=targets,
    )
