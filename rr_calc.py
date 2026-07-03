"""Helper untuk menghitung realized RR (RR yang benar-benar tercapai saat
signal ditutup).

Dipakai oleh:
- monitor.py  -> saat auto-close (WIN/LOSS/MIXED), dihitung dari status
                 target (TP mana saja yang HIT).
- main.py     -> saat manual close via /close, dihitung langsung dari
                 harga penutupan manual (lihat compute_manual_rr).
- recap.py    -> fallback kalau ada data lama yang belum punya realized_rr
                 tersimpan di kolom signals.realized_rr.

Asumsi position sizing: modal dibagi rata ke tiap level TP (mis. kalau ada
3 level TP, tiap level mewakili 1/3 posisi).
"""


def compute_realized_rr(result: str, targets: list[dict]) -> float:
    """RR realized untuk close otomatis (WIN/LOSS/MIXED) berdasarkan status
    tiap level target (HIT/PENDING)."""
    if not targets:
        return -1.0 if result == "LOSS" else 0.0

    n = len(targets)
    if result == "WIN":
        return sum(t["rr"] for t in targets) / n
    # LOSS atau MIXED
    total = 0.0
    for t in targets:
        total += t["rr"] if t["status"] == "HIT" else -1.0
    return total / n


def compute_manual_rr(direction: str, entry: float, stoploss: float, close_price: float) -> float:
    """RR realized untuk close manual (/close), dihitung langsung dari
    jarak harga penutupan ke entry, dalam satuan risk (entry-stoploss)."""
    risk = abs(entry - stoploss)
    if risk == 0:
        return 0.0
    if direction == "LONG":
        return (close_price - entry) / risk
    return (entry - close_price) / risk
