"""Client sederhana untuk ambil harga terkini dari MEXC Spot public API.

Tidak butuh API key karena cuma baca harga (bukan trading).
"""
import httpx

import config


async def get_all_prices() -> dict[str, float]:
    """Ambil harga terakhir SEMUA symbol dalam satu call (efisien untuk
    banyak signal sekaligus, hindari rate limit).

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


async def get_price(symbol: str) -> float | None:
    """Ambil harga satu symbol saja (dipakai untuk cek cepat / debugging)."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(config.MEXC_TICKER_ALL_URL, params={"symbol": symbol})
        resp.raise_for_status()
        data = resp.json()
    try:
        return float(data["price"])
    except (KeyError, ValueError, TypeError):
        return None
