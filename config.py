import os
from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default=None, required: bool = False):
    val = os.getenv(name, default)
    if required and not val:
        raise RuntimeError(f"Environment variable '{name}' wajib diisi (lihat .env.example)")
    return val


TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN", required=True)
TELEGRAM_CHANNEL_ID = _get("TELEGRAM_CHANNEL_ID", required=True)

# Daftar user_id Telegram (dipisah koma) yang boleh pakai command sensitif
# (/cancel, /close, /rekap_harian, /rekap_bulanan). Isi lewat @userinfobot
# atau sejenisnya untuk dapat user_id kamu. Kalau dikosongkan, SEMUA
# command sensitif akan ditolak (fail-safe: tidak ada admin yang keliru
# jadi malah tidak ada proteksi sama sekali).
_raw_admin_ids = _get("TELEGRAM_ADMIN_IDS", "")
TELEGRAM_ADMIN_IDS = {
    int(x.strip()) for x in _raw_admin_ids.split(",") if x.strip()
}

SUPABASE_URL = _get("SUPABASE_URL", required=True)
SUPABASE_KEY = _get("SUPABASE_KEY", required=True)

DEFAULT_RR_MAX = float(_get("DEFAULT_RR_MAX", "3"))  # generate TP1..TPn sampai RR ini
POLL_INTERVAL_SECONDS = int(_get("POLL_INTERVAL_SECONDS", "10"))
DEFAULT_QUOTE = _get("DEFAULT_QUOTE", "USDT").upper()

TIMEZONE = _get("TIMEZONE", "Asia/Jakarta")
DAILY_RECAP_HOUR = int(_get("DAILY_RECAP_HOUR", "23"))
DAILY_RECAP_MINUTE = int(_get("DAILY_RECAP_MINUTE", "59"))
MONTHLY_RECAP_DAY = int(_get("MONTHLY_RECAP_DAY", "1"))
MONTHLY_RECAP_HOUR = int(_get("MONTHLY_RECAP_HOUR", "8"))
MONTHLY_RECAP_MINUTE = int(_get("MONTHLY_RECAP_MINUTE", "0"))

MEXC_TICKER_ALL_URL = "https://api.mexc.com/api/v3/ticker/price"
