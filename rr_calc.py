"""Helper untuk menghitung realized RR & realized % (RR/persentase yang
benar-benar tercapai saat signal ditutup).

Dipakai oleh:
- monitor.py  -> saat auto-close (WIN/LOSS/MIXED), dihitung dari status
                 target (TP mana saja yang HIT).
- main.py     -> saat manual close via /close, dihitung langsung dari
                 harga penutupan manual (lihat compute_manual_rr /
                 compute_manual_pct).
- recap.py    -> total akumulasi rekap harian/bulanan pakai persentase
                 (compute_realized_pct / compute_manual_pct), BUKAN RR,
                 karena 1R beda-beda jaraknya tiap signal jadi tidak bisa
                 dijumlah apple-to-apple. RR tetap dipakai buat detail
                 per-signal (fallback kalau realized_rr belum tersimpan).

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


def _pct_change(direction: str, entry: float, price: float) -> float:
    """Persentase perubahan harga dari entry ke price, searah profit (+)
    sesuai direction."""
    if not entry:
        return 0.0
    change = (price - entry) / entry * 100
    if direction == "SHORT":
        change = -change
    return change


def compute_realized_pct(result: str, direction: str, entry: float, stoploss: float,
                          targets: list[dict]) -> float:
    """Persentase gain/loss realized untuk close otomatis (WIN/LOSS/MIXED).

    Ini SENGAJA dipisah dari compute_realized_rr karena 1R itu jaraknya
    beda-beda tiap signal (tergantung jarak entry-stoploss), jadi RR dari
    signal yang satu tidak bisa langsung dijumlah/dibandingkan apple-to-apple
    dengan RR signal lain. Persentase harga itu netral, jadi lebih pas
    dipakai buat akumulasi total (mis. total return rekap harian).

    Asumsi position sizing sama seperti compute_realized_rr: modal dibagi
    rata ke tiap level TP. Untuk level yang belum HIT saat SL kena (kasus
    MIXED/LOSS), porsi itu dianggap closed di harga stoploss.
    """
    if not targets:
        pct = _pct_change(direction, entry, stoploss)
        return pct if result != "WIN" else 0.0

    n = len(targets)
    total = 0.0
    for t in targets:
        if result == "WIN" or t["status"] == "HIT":
            price = t.get("hit_price") or t["price"]
        else:
            price = stoploss
        total += _pct_change(direction, entry, price)
    return total / n


def compute_manual_pct(direction: str, entry: float, close_price: float) -> float:
    """Persentase gain/loss realized untuk close manual (/close)."""
    return _pct_change(direction, entry, close_price)
