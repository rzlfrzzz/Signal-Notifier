"""Wrapper akses Supabase untuk tabel `signals` dan `signal_targets`."""
from datetime import datetime, timezone
from typing import Optional

from supabase import create_client, Client

import config
from signal_parser import TargetLevel

_client: Optional[Client] = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    return _client


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_signal(*, message_id: int, chat_id: int, pair: str, symbol: str,
                   direction: str, entry: float, stoploss: float,
                   raw_message: str) -> dict:
    client = get_client()
    row = {
        "message_id": message_id,
        "chat_id": chat_id,
        "pair": pair,
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "stoploss": stoploss,
        "status": "PENDING",
        "raw_message": raw_message,
    }
    res = client.table("signals").insert(row).execute()
    return res.data[0]


def insert_targets(signal_id: int, targets: list[TargetLevel]) -> list[dict]:
    client = get_client()
    rows = [
        {"signal_id": signal_id, "level": t.level, "rr": t.rr, "price": t.price, "status": "PENDING"}
        for t in targets
    ]
    res = client.table("signal_targets").insert(rows).execute()
    return res.data


def get_open_signals() -> list[dict]:
    """Signal yang masih PENDING (nunggu entry) atau ACTIVE (nunggu SL/TP)."""
    client = get_client()
    res = (
        client.table("signals")
        .select("*")
        .in_("status", ["PENDING", "ACTIVE"])
        .execute()
    )
    return res.data


def get_targets_for_signals(signal_ids: list[int]) -> dict[int, list[dict]]:
    """Ambil SEMUA target (PENDING & HIT) untuk daftar signal_id, dikelompokkan
    per signal_id, terurut berdasarkan level."""
    if not signal_ids:
        return {}
    client = get_client()
    res = (
        client.table("signal_targets")
        .select("*")
        .in_("signal_id", signal_ids)
        .order("level")
        .execute()
    )
    grouped: dict[int, list[dict]] = {}
    for row in res.data:
        grouped.setdefault(row["signal_id"], []).append(row)
    return grouped


def get_all_targets_for_signal(signal_id: int) -> list[dict]:
    client = get_client()
    res = (
        client.table("signal_targets")
        .select("*")
        .eq("signal_id", signal_id)
        .order("level")
        .execute()
    )
    return res.data


def mark_active(signal_id: int, price: float):
    client = get_client()
    client.table("signals").update({
        "status": "ACTIVE",
        "entry_hit_at": _now_iso(),
        "last_price": price,
    }).eq("id", signal_id).execute()


def mark_target_hit(target_id: int, hit_price: float):
    client = get_client()
    client.table("signal_targets").update({
        "status": "HIT",
        "hit_at": _now_iso(),
        "hit_price": hit_price,
    }).eq("id", target_id).execute()


def close_signal(signal_id: int, *, result: str, price: float):
    """result: 'WIN' (semua TP tercapai) | 'LOSS' (SL kena, belum ada TP hit)
    | 'MIXED' (SL kena, tapi sebagian TP sudah tercapai duluan)."""
    client = get_client()
    client.table("signals").update({
        "status": "CLOSED",
        "result": result,
        "last_price": price,
        "closed_at": _now_iso(),
    }).eq("id", signal_id).execute()


def update_last_price(signal_id: int, price: float):
    client = get_client()
    client.table("signals").update({"last_price": price}).eq("id", signal_id).execute()


def get_closed_signals_between(start_iso: str, end_iso: str) -> list[dict]:
    client = get_client()
    res = (
        client.table("signals")
        .select("*")
        .gte("closed_at", start_iso)
        .lt("closed_at", end_iso)
        .eq("status", "CLOSED")
        .execute()
    )
    return res.data
