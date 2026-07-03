-- ============================================================
-- SKEMA FRESH (kalau kamu belum pernah setup Supabase sama sekali)
-- Jalankan semua bagian di bawah ini di Supabase SQL Editor.
-- ============================================================

create table if not exists signals (
    id              bigserial primary key,
    message_id      bigint,
    chat_id         bigint,
    pair            text not null,          -- tampilan asli, mis. "RE/USDT"
    symbol          text not null,           -- symbol MEXC, mis. "REUSDT"
    direction       text not null check (direction in ('LONG','SHORT')),
    entry           numeric not null,
    stoploss        numeric not null,
    status          text not null default 'PENDING',
                    -- PENDING -> ACTIVE -> CLOSED
                    -- (PENDING = entry belum kesentuh, ACTIVE = sudah entry,
                    --  CLOSED = SL kena ATAU semua TP level tercapai)
    result          text,                    -- 'WIN' | 'LOSS' | 'MIXED' | null selama belum closed
    raw_message     text,
    last_price      numeric,
    created_at      timestamptz not null default now(),
    entry_hit_at    timestamptz,
    closed_at       timestamptz
);

create index if not exists idx_signals_status on signals(status);
create index if not exists idx_signals_symbol on signals(symbol);
create index if not exists idx_signals_closed_at on signals(closed_at);

-- Multi level Take Profit per signal (TP1 = RR 1:1, TP2 = RR 1:2, dst)
create table if not exists signal_targets (
    id              bigserial primary key,
    signal_id       bigint not null references signals(id) on delete cascade,
    level           integer not null,        -- 1, 2, 3, ...
    rr              numeric not null,        -- RR di level ini
    price           numeric not null,        -- harga target level ini
    status          text not null default 'PENDING',  -- PENDING | HIT
    hit_at          timestamptz,
    hit_price       numeric,                 -- harga aktual saat tercapai
    unique(signal_id, level)
);

create index if not exists idx_signal_targets_signal_id on signal_targets(signal_id);
create index if not exists idx_signal_targets_status on signal_targets(status);


-- ============================================================
-- MIGRASI (kalau tabel `signals` KAMU SUDAH ADA dan sudah dipakai/ada data,
-- seperti kondisi kamu sekarang). Jalankan hanya bagian ini.
-- Aman dijalankan berkali-kali (pakai IF EXISTS / IF NOT EXISTS).
-- ============================================================

-- Kolom take_profit & rr lama sudah tidak dipakai kode baru, jadikan nullable
-- supaya tidak mengganggu (data lama tetap ada, cuma tidak diisi lagi).
alter table signals alter column take_profit drop not null;
alter table signals alter column rr drop not null;

-- Perbarui constraint status: status lama 'TP_HIT'/'SL_HIT' diganti 'CLOSED'
-- Data lama yang statusnya TP_HIT/SL_HIT tetap dianggap closed oleh query baru
-- (lihat catatan di README), tidak perlu diubah manual kecuali kamu mau rapikan.

create table if not exists signal_targets (
    id              bigserial primary key,
    signal_id       bigint not null references signals(id) on delete cascade,
    level           integer not null,
    rr              numeric not null,
    price           numeric not null,
    status          text not null default 'PENDING',
    hit_at          timestamptz,
    hit_price       numeric,
    unique(signal_id, level)
);

create index if not exists idx_signal_targets_signal_id on signal_targets(signal_id);
create index if not exists idx_signal_targets_status on signal_targets(status);
