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
from typing import Optional

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


_STOCK_SUFFIX = "STOCK"


def stock_symbol_variant(symbol: str) -> Optional[str]:
    """Konversi symbol biasa ('MUUSDT', 'SAMSUNGUSDT') ke varian symbol
    kategori Stock di MEXC ('MUSTOCKUSDT', 'SAMSUNGSTOCKUSDT') dengan
    menyisipkan 'STOCK' di base asset, sebelum quote asset.

    Ini karena base asset kategori tokenized stock/stock futures di MEXC
    memang pakai suffix 'STOCK' (mis. ticker asli MU jadi MUSTOCK di
    MEXC), beda dari ticker mentah yang tercantum di teks signal (cuma
    'MU') yang dipakai signal_parser buat build symbol awal ('MUUSDT').
    Return None kalau symbol sudah dalam bentuk stock variant atau
    formatnya tidak dikenali (tidak diakhiri quote asset), supaya caller
    tidak perlu query dua kali ke symbol yang sama."""
    quote = config.DEFAULT_QUOTE
    if not (quote and symbol.endswith(quote) and len(symbol) > len(quote)):
        return None
    base = symbol[: -len(quote)]
    if base.endswith(_STOCK_SUFFIX):
        return None
    return f"{base}{_STOCK_SUFFIX}{quote}"


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
    """Gabungan harga Spot + Futures (fallback). Sumber yang DIUTAMAKAN
    kalau satu symbol kebetulan ada di keduanya diatur lewat
    config.PRICE_SOURCE_PRIORITY ("futures" secara default — lihat catatan
    di config.py kenapa; ganti ke "spot" via .env kalau perlu).

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

    if config.PRICE_SOURCE_PRIORITY == "spot":
        # Spot menang kalau symbol ada di keduanya.
        combined = {**futures_prices, **spot_prices}
    else:
        # Default: Futures menang kalau symbol ada di keduanya (lihat
        # docstring/config.py — level signal ditarik dari chart Futures).
        combined = {**spot_prices, **futures_prices}

    return combined, futures_only_symbols


async def _try_spot_price(symbol: str) -> Optional[float]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(config.MEXC_TICKER_ALL_URL, params={"symbol": symbol})
        resp.raise_for_status()
        data = resp.json()
    try:
        return float(data["price"])
    except (KeyError, ValueError, TypeError):
        return None


async def _try_futures_price(symbol: str) -> Optional[float]:
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


async def get_price(symbol: str) -> Optional[float]:
    """Ambil harga satu symbol saja (dipakai command /close, fetch harga
    awal saat signal baru dibuat, & debugging cepat). Urutan coba
    Spot-dulu-atau-Futures-dulu ikut config.PRICE_SOURCE_PRIORITY (lihat
    get_combined_prices), lalu fallback ke varian Stock kalau dua-duanya
    tidak ketemu. Varian Stock (mis. 'MUUSDT' -> 'MUSTOCKUSDT') perlu
    dicoba juga karena base asset kategori tokenized stock/stock futures
    di MEXC pakai suffix 'STOCK' yang tidak tercantum di ticker mentah
    dari teks signal (lihat docstring stock_symbol_variant)."""
    if config.PRICE_SOURCE_PRIORITY == "spot":
        primary, secondary = _try_spot_price, _try_futures_price
    else:
        primary, secondary = _try_futures_price, _try_spot_price

    price = await primary(symbol)
    if price is not None:
        return price

    price = await secondary(symbol)
    if price is not None:
        return price

    variant = stock_symbol_variant(symbol)
    if variant is None:
        return None

    price = await primary(variant)
    if price is not None:
        return price

    return await secondary(variant)
