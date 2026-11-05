-- 01_schema.sql — star schema for daily Italian fuel prices, plus ETL bookkeeping tables.
-- Created by `python -m etl init-db`. Safe to run again (IF NOT EXISTS everywhere).

CREATE SCHEMA IF NOT EXISTS fuel_dw;
SET search_path TO fuel_dw;

-- ----------------------------------------------------------------- dimensions
CREATE TABLE IF NOT EXISTS dim_date (
    date_key     INT PRIMARY KEY,          -- yyyymmdd
    full_date    DATE NOT NULL UNIQUE,
    year         SMALLINT NOT NULL,
    quarter      SMALLINT NOT NULL,
    month        SMALLINT NOT NULL,
    iso_week     SMALLINT NOT NULL,
    weekday      SMALLINT NOT NULL,        -- 1 = Monday
    is_weekend   BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_region (
    province     CHAR(2) PRIMARY KEY,
    region       TEXT NOT NULL,
    macro_area   TEXT NOT NULL             -- North / Centre / South & Islands (ISTAT)
);

CREATE TABLE IF NOT EXISTS dim_station (
    station_id     INT PRIMARY KEY,        -- idImpianto
    operator       TEXT,
    brand          TEXT,
    station_type   TEXT,                   -- Road / Motorway
    station_name   TEXT,
    address        TEXT,
    municipality   TEXT,
    province       CHAR(2) REFERENCES dim_region,
    latitude       DOUBLE PRECISION,
    longitude      DOUBLE PRECISION,
    in_registry    BOOLEAN NOT NULL,       -- false = seen in price files but not in the registry snapshot
    registry_date  DATE,                   -- date of the registry snapshot that described it
    first_price_date DATE,
    last_price_date  DATE
);

CREATE TABLE IF NOT EXISTS dim_fuel (
    fuel_id      SMALLSERIAL PRIMARY KEY,
    fuel_name    TEXT NOT NULL UNIQUE,     -- descCarburante as published
    fuel_group   TEXT NOT NULL             -- Petrol / Diesel / Premium petrol / Premium diesel / LPG / CNG / LNG / HVO / Other
);

-- ----------------------------------------------------------------- facts
-- One row per day × station × fuel × service: the price in force at 8:00.
CREATE TABLE IF NOT EXISTS fact_price (
    date_key       INT      NOT NULL REFERENCES dim_date,
    station_id     INT      NOT NULL REFERENCES dim_station,
    fuel_id        SMALLINT NOT NULL REFERENCES dim_fuel,
    is_self        BOOLEAN  NOT NULL,
    price_eur      NUMERIC(6,3) NOT NULL,
    price_updated  TIMESTAMP NOT NULL,     -- when the station last reported this price
    PRIMARY KEY (date_key, station_id, fuel_id, is_self)
);

-- One row per day: crude oil and exchange rate.
CREATE TABLE IF NOT EXISTS fact_market (
    date_key       INT PRIMARY KEY REFERENCES dim_date,
    brent_usd_bbl  NUMERIC(8,2),
    usd_per_eur    NUMERIC(8,4),
    brent_eur_litre NUMERIC(8,4)           -- brent_usd_bbl / usd_per_eur / 158.987
);

-- ----------------------------------------------------------------- ETL bookkeeping
CREATE TABLE IF NOT EXISTS etl_run (
    run_id        SERIAL PRIMARY KEY,
    command       TEXT NOT NULL,
    started_at    TIMESTAMP NOT NULL DEFAULT now(),
    finished_at   TIMESTAMP,
    status        TEXT NOT NULL DEFAULT 'running',   -- running / success / failed
    rows_read     BIGINT DEFAULT 0,
    rows_loaded   BIGINT DEFAULT 0,
    rows_quarantined BIGINT DEFAULT 0,
    message       TEXT
);

-- One row per price file loaded: makes loads idempotent.
CREATE TABLE IF NOT EXISTS etl_loaded_file (
    price_date    DATE PRIMARY KEY,
    source        TEXT NOT NULL,
    sha256        CHAR(64) NOT NULL,
    rows_read     INT NOT NULL,
    rows_loaded   INT NOT NULL,
    rows_quarantined INT NOT NULL,
    run_id        INT REFERENCES etl_run,
    loaded_at     TIMESTAMP NOT NULL DEFAULT now()
);

-- Rows that failed validation, kept with the reason (never silently dropped).
CREATE TABLE IF NOT EXISTS etl_quarantine (
    id            BIGSERIAL PRIMARY KEY,
    run_id        INT REFERENCES etl_run,
    price_date    DATE,
    source        TEXT NOT NULL,
    line_no       INT,
    raw_line      TEXT,
    reason        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_fact_price_station ON fact_price (station_id);
CREATE INDEX IF NOT EXISTS ix_quarantine_date ON etl_quarantine (price_date);
