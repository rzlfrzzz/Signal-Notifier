"""Client sederhana untuk ambil harga terkini dari MEXC.

Sumber utama: MEXC Spot public API (tidak butuh API key, cuma baca harga).

Beberapa pair yang muncul di signal ternyata TIDAK listing di Spot sama
sekali, cuma di MEXC Futures (kontrak perpetual) — misalnya token yang
baru listing Futures duluan, atau produk derivatif seperti SAMSUNGUSDT
yang memang tidak punya versi Spot. Kalau ini kejadian, symbol itu tidak
akan pernah muncul di response Spot API, jadi `monitor.py` akan selalu
skip signal tersebut TANPA notifikasi apapun (seolah bot ngadat padahal
sebenarnya harga di sumber lain).

Untuk itu, `get_combined_prices()` di bawah ini gabungkan harga Spot +
Futures (fallback), supaya pair yang cuma ada di Futures tetap kepantau.
"""
import logging

import httpx

import config

logger = logging.getLogger(__name__)


async def get_all_spot_prices() -> dict[str, float]:
    """Ambil harga terakhir SEMUA symbol Spot dalam satu call (efisien
    untuk banyak signal sekaligus, hindari rate limit).

    Return: {"BTCUSDT": 65000.1, "REUSDT": 0.63, ...}
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(config.MEXC_TICKER_ALL_URL)
        resp.raise_for_status()
        data = resp.json()

    prices = {}
    for item in data:
        try:
            prices[item["symbol"]] = float(item["price"])
        except (KeyError, ValueError, TypeError):
            continue
    return prices


def _futures_symbol_to_spot_style(futures_symbol: str) -> str:
    """Konversi symbol format Futures ('VVV_USDT') ke format tanpa
    underscore ('VVVUSDT') supaya konsisten dengan symbol Spot yang
    dipakai di seluruh aplikasi (signal_parser, database, dst)."""
    return futures_symbol.replace("_", "")


def _spot_symbol_to_futures_style(symbol: str) -> str:
    """Kebalikan dari _futures_symbol_to_spot_style: 'VVVUSDT' -> 'VVV_USDT'.
    Asumsi quote asset ada di akhir string sesuai config.DEFAULT_QUOTE."""
    quote = config.DEFAULT_QUOTE
    if quote and symbol.endswith(quote) and len(symbol) > len(quote):
        base = symbol[: -len(quote)]
        return f"{base}_{quote}"
    return symbol


async def get_all_futures_prices() -> dict[str, float]:
    """Ambil harga terakhir SEMUA kontrak Futures dalam satu call.

    Return: {"VVVUSDT": 5.44, "SAMSUNGUSDT": 61200.0, ...} (symbol sudah
    dikonversi ke format tanpa underscore, konsisten dengan Spot)."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(config.MEXC_FUTURES_TICKER_ALL_URL)
        resp.raise_for_status()
        payload = resp.json()

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return {}

    prices = {}
    for item in data:
        try:
            symbol = _futures_symbol_to_spot_style(str(item["symbol"]))
            prices[symbol] = float(item["lastPrice"])
        except (KeyError, ValueError, TypeError):
            continue
    return prices


async def get_combined_prices() -> tuple[dict[str, float], set[str]]:
    """Gabungan harga Spot + Futures (fallback). Spot diprioritaskan kalau
    satu symbol kebetulan ada di keduanya.

    Return: (prices, futures_only_symbols)
    - prices             : dict harga gabungan, siap dipakai monitor.py
                            persis seperti get_all_spot_prices() dulu.
    - futures_only_symbols : set symbol yang harganya CUMA didapat dari
                            Futures (tidak ada di Spot sama sekali) —
                            dipakai caller buat kasih notifikasi/log kalau
                            perlu.

    Kalau fetch Futures gagal (network error dsb), fail-soft: tetap lanjut
    pakai Spot saja supaya satu sumber down tidak menghentikan seluruh
    monitoring."""
    spot_prices = await get_all_spot_prices()

    try:
        futures_prices = await get_all_futures_prices()
    except Exception as e:
        logger.warning("Gagal ambil harga Futures MEXC (lanjut pakai Spot saja): %s", e)
        futures_prices = {}

    futures_only_symbols = set(futures_prices) - set(spot_prices)
    combined = {**futures_prices, **spot_prices}
    return combined, futures_only_symbols


async def get_price(symbol: str) -> float | None:
    """Ambil harga satu symbol saja (dipakai command /close & debugging
    cepat). Coba Spot dulu, fallback ke Futures kalau symbol itu ternyata
    bukan pair Spot (mis. SAMSUNGUSDT)."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(config.MEXC_TICKER_ALL_URL, params={"symbol": symbol})
        resp.raise_for_status()
        data = resp.json()
    try:
        return float(data["price"])
    except (KeyError, ValueError, TypeError):
        pass

    futures_symbol = _spot_symbol_to_futures_style(symbol)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                config.MEXC_FUTURES_TICKER_ALL_URL, params={"symbol": futures_symbol}
            )
            resp.raise_for_status()
            payload = resp.json()
        return float(payload["data"]["lastPrice"])
    except Exception as e:
        logger.warning("Gagal ambil harga Futures MEXC untuk %s: %s", symbol, e)
        return None
