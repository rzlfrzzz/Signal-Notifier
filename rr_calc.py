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

Asumsi position sizing (TIDAK lagi dibagi rata):
- TP1 menutup 50% posisi.
- TP2 menutup 25% posisi lagi, dan begitu TP2 HIT, SL sisa posisi (25%
  terakhir) dianggap dipindah ke entry (Stop Loss Break Even / SLBE) —
  praktik umum trading untuk mengunci profit & meminimalisir kerugian.
- TP3 (dan level tambahan lain kalau RR-nya panjang) menutup sisa posisi
  terakhir, dibagi rata di antara level-level tersebut.

Kalau cuma ada 1 level TP, level itu otomatis menutup 100% posisi. Kalau
cuma ada 2 level, TP1 & TP2 masing-masing 50%.

Efek SLBE terhadap perhitungan: kalau SL akhirnya kena SETELAH TP2 HIT,
porsi TP yang belum sempat tercapai (mis. TP3 dst) dihitung closed di
harga ENTRY (RR = 0, bukan RR = -1), karena secara real, posisi itu sudah
diamankan di breakeven begitu TP2 tercapai. Kalau SL kena SEBELUM TP2
HIT, porsi yang belum tercapai tetap dihitung rugi penuh (-1R) seperti
biasa, karena SL belum sempat dipindah.
"""


def _level_weights(n: int) -> list[float]:
    """Bobot porsi posisi per level TP, terurut TP1..TPn.

    - n == 1 -> [1.0]                    (TP1 menutup semua posisi)
    - n == 2 -> [0.5, 0.5]                (TP1 & TP2 masing-masing 50%)
    - n >= 3 -> [0.5, sisa dibagi rata di antara level ke-2 s/d ke-n]
      mis. n == 3 -> [0.5, 0.25, 0.25]  (TP1 50% / TP2 25% / TP3 25%)
    """
    if n <= 0:
        return []
    if n == 1:
        return [1.0]
    rest = 0.5 / (n - 1)
    return [0.5] + [rest] * (n - 1)


def _slbe_active(targets: list[dict]) -> bool:
    """True kalau TP2 sudah HIT -> SL sisa posisi dianggap sudah dipindah
    ke entry (breakeven)."""
    return any(t["level"] == 2 and t["status"] == "HIT" for t in targets)


def compute_realized_rr(result: str, targets: list[dict]) -> float:
    """RR realized untuk close otomatis (WIN/LOSS/MIXED) berdasarkan status
    tiap level target (HIT/PENDING), dibobotkan per porsi posisi (lihat
    _level_weights) dan memperhitungkan SLBE setelah TP2 HIT."""
    if not targets:
        return -1.0 if result == "LOSS" else 0.0

    targets = sorted(targets, key=lambda t: t["level"])
    weights = _level_weights(len(targets))
    slbe = _slbe_active(targets)

    if result == "WIN":
        return sum(w * t["rr"] for w, t in zip(weights, targets))

    # LOSS atau MIXED
    total = 0.0
    for w, t in zip(weights, targets):
        if t["status"] == "HIT":
            total += w * t["rr"]
        elif slbe:
            total += w * 0.0  # SL sudah dipindah ke entry -> porsi ini breakeven
        else:
            total += w * -1.0
    return total


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

    Asumsi position sizing sama seperti compute_realized_rr: TP1=50%,
    TP2=25%, TP3 dst=sisanya rata (lihat _level_weights). Untuk level yang
    belum HIT saat SL kena:
    - kalau TP2 sudah HIT (SLBE aktif), porsi itu dianggap closed di harga
      ENTRY (breakeven), bukan di harga stoploss.
    - kalau belum, porsi itu dianggap closed di harga stoploss seperti biasa.
    """
    if not targets:
        pct = _pct_change(direction, entry, stoploss)
        return pct if result != "WIN" else 0.0

    targets = sorted(targets, key=lambda t: t["level"])
    weights = _level_weights(len(targets))
    slbe = _slbe_active(targets)

    total = 0.0
    for w, t in zip(weights, targets):
        if result == "WIN" or t["status"] == "HIT":
            price = t.get("hit_price") or t["price"]
        elif slbe:
            price = entry
        else:
            price = stoploss
        total += w * _pct_change(direction, entry, price)
    return total


def compute_manual_pct(direction: str, entry: float, close_price: float) -> float:
    """Persentase gain/loss realized untuk close manual (/close)."""
    return _pct_change(direction, entry, close_price)
