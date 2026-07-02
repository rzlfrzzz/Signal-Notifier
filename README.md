# MEXC Signal Monitor Bot

Bot Telegram untuk memantau signal trading yang kamu post ke channel:
- Deteksi otomatis **Entry Hit**, **Stoploss Hit**, **Take Profit Hit** (harga real-time dari MEXC)
- **Rekap harian & bulanan** otomatis ke channel
- Data disimpan di **Supabase**

## Cara Kerja Singkat

1. Kamu posting signal ke channel seperti biasa (format yang sudah kamu pakai, contoh di bawah).
2. Bot (yang jadi admin channel) otomatis membaca post itu, parse pair/entry/SL,
   hitung TP dari **RR default 1:3** (atau RR eksplisit kalau kamu tulis, mis. `RR 1:5`),
   lalu simpan ke Supabase sebagai posisi `PENDING`.
3. Setiap `POLL_INTERVAL_SECONDS` detik, bot cek harga MEXC:
   - Kalau harga menyentuh **entry** -> status jadi `ACTIVE`, bot kirim pesan "Entry Hit" ke channel.
   - Kalau harga (setelah aktif) menyentuh **SL** atau **TP** -> posisi ditutup, bot kirim hasil ke channel.
4. Jam yang kamu tentukan tiap hari -> bot kirim **rekap harian**.
5. Tanggal 1 tiap bulan -> bot kirim **rekap bulanan** (bulan sebelumnya).

## Format Signal yang Didukung

```
🔥 $RE/USDT (LONG) 🟢

Entry : 0.6325
Stoploss : 0.6000
Target : on chart
```

```
🟢 LONG SIGNAL ↗️

Pair: $NOM
Entry: 0.002236
Stop Loss: 0.001414

📌 Take Profit levels are marked on the chart above.
```

Format SHORT sama persis, tinggal ganti kata/emoji jadi SHORT. Parser mengenali
kata **LONG**/**SHORT** di mana saja dalam pesan, label **Entry**, **Stop Loss** /
**Stoploss** / **SL**, dan pair setelah tanda `$` (dengan atau tanpa label `Pair:`).

Kalau pesan channel TIDAK cocok pola ini (mis. pengumuman biasa), bot otomatis
mengabaikannya — tidak akan tersimpan sebagai signal.

## Setup

### 1. Buat Bot Telegram
- Chat ke [@BotFather](https://t.me/BotFather) -> `/newbot` -> catat **token**-nya.
- Tambahkan bot ke channel kamu sebagai **Administrator**
  (minimal permission: **Post Messages** dan bisa membaca pesan channel).
  Ini wajib, karena Telegram hanya mengirim update `channel_post` ke bot yang jadi admin.

### 2. Setup Supabase
- Buat project di [supabase.com](https://supabase.com).
- Buka **SQL Editor**, jalankan isi file `schema.sql` di repo ini.
- Ambil **Project URL** dan **service_role key** (Settings -> API).
  > Pakai `service_role` key (bukan `anon`), karena bot butuh full write access
  > dan berjalan di server, bukan di browser.

### 3. Konfigurasi
```bash
cp .env.example .env
```
Isi semua value di `.env`:
- `TELEGRAM_BOT_TOKEN` — dari BotFather
- `TELEGRAM_CHANNEL_ID` — `@username_channel` (kalau channel publik) atau numeric id
  (kalau private, mulai dengan `-100...`)
- `SUPABASE_URL`, `SUPABASE_KEY`
- `DEFAULT_RR` — default `3` (RR 1:3)
- `POLL_INTERVAL_SECONDS` — default `10` detik
- `TIMEZONE`, jam rekap harian/bulanan

### 4. Install & Jalankan
```bash
pip install -r requirements.txt
python main.py
```

Jalankan terus-menerus pakai `systemd`, `pm2`, `screen`/`tmux`, atau Docker
di server kamu (VPS kecil sudah cukup).

## Command Manual

Chat langsung ke bot (bukan di channel):
- `/status` — lihat semua posisi yang sedang dipantau
- `/rekap_harian` — trigger manual rekap hari ini
- `/rekap_bulanan` — trigger manual rekap bulan sebelumnya

## Catatan & Batasan Penting

- **Harga dari MEXC Spot public API** (`/api/v3/ticker/price`), tidak perlu API key
  karena bot hanya membaca harga, tidak melakukan trading otomatis.
- Deteksi hit pakai **crossing check** antar-poll (bandingkan harga sebelum & sesudah),
  jadi tetap akurat walau harga "lompat" melewati level di antara dua polling —
  tapi kalau harga gap terlalu ekstrem (misal delisting/flash crash), tetap mungkin
  ada selisih kecil dari harga real saat itu. Perkecil `POLL_INTERVAL_SECONDS`
  kalau butuh presisi lebih tinggi (dengan trade-off makin sering call API).
- Bot ini **hanya memantau harga**, tidak eksekusi order — cocok untuk channel
  signal manual seperti punya kamu.
- Symbol MEXC dibentuk otomatis dari ticker (mis. `$RE` -> `REUSDT`). Kalau pair kamu
  bukan quote USDT, tulis eksplisit mis. `$RE/BUSD`.
- RR realized di rekap dihitung sederhana: **menang = +RR**, **kalah = -1R**
  (asumsi risk per trade konsisten, sesuai catatan "risk 1-3% per trade" di signal kamu).

## Struktur File

```
config.py          # baca .env
database.py         # semua query ke Supabase
mexc_client.py      # ambil harga dari MEXC
signal_parser.py    # parse teks signal -> ParsedSignal
monitor.py          # loop cek entry/SL/TP
recap.py            # generate & kirim rekap harian/bulanan
main.py             # entry point, handler Telegram, scheduler
schema.sql           # skema tabel Supabase
```
