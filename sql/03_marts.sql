-- 03_marts.sql — analysis-ready views, rebuilt by `python -m etl refresh-marts`.
-- Business rules live HERE, not in the load: the warehouse keeps every valid price as published,
-- and the marts decide what counts as "clean". Parameters in {{braces}} come from config.yaml.
SET search_path TO fuel_dw;

DROP MATERIALIZED VIEW IF EXISTS mart_region_monthly;
DROP MATERIALIZED VIEW IF EXISTS mart_daily_national;
DROP MATERIALIZED VIEW IF EXISTS mart_market_daily;
DROP VIEW IF EXISTS v_price_clean;

-- Clean standard petrol and diesel prices.
--   * stale: not confirmed for more than {max_price_age_days} days
--   * outlier: outside [{outlier_low}, {outlier_high}] × the day's national self-service median of that fuel
--   * Livigno excluded (duty-free zone, not comparable)
CREATE VIEW v_price_clean AS
WITH std AS (
    SELECT f.*, d.fuel_group
    FROM fact_price f
    JOIN dim_fuel d USING (fuel_id)
    WHERE d.fuel_group IN ('Petrol', 'Diesel')
),
day_median AS (
    SELECT date_key, fuel_id, PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_eur) AS self_median
    FROM std WHERE is_self
    GROUP BY date_key, fuel_id
)
SELECT s.date_key, dd.full_date, s.station_id, s.fuel_group,
       CASE WHEN s.is_self THEN 'Self-service' ELSE 'Served' END AS service,
       s.price_eur, st.station_type, st.brand, st.province
FROM std s
JOIN day_median m USING (date_key, fuel_id)
JOIN dim_date dd USING (date_key)
JOIN dim_station st USING (station_id)
WHERE dd.full_date - s.price_updated::date <= {max_price_age_days}
  AND s.price_eur BETWEEN {outlier_low} * m.self_median AND {outlier_high} * m.self_median
  AND COALESCE(st.municipality, '') <> 'LIVIGNO';

-- Crude oil and exchange rate for EVERY calendar day: weekends and holidays carry the last known value.
CREATE MATERIALIZED VIEW mart_market_daily AS
WITH m AS (
    SELECT d.date_key, d.full_date, f.brent_usd_bbl, f.usd_per_eur,
           COUNT(f.brent_usd_bbl) OVER (ORDER BY d.full_date) AS gb,
           COUNT(f.usd_per_eur)   OVER (ORDER BY d.full_date) AS gf
    FROM dim_date d LEFT JOIN fact_market f USING (date_key)
)
SELECT date_key, full_date,
       FIRST_VALUE(brent_usd_bbl) OVER (PARTITION BY gb ORDER BY full_date) AS brent_usd_bbl,
       FIRST_VALUE(usd_per_eur)   OVER (PARTITION BY gf ORDER BY full_date) AS usd_per_eur,
       ROUND(FIRST_VALUE(brent_usd_bbl) OVER (PARTITION BY gb ORDER BY full_date)
           / FIRST_VALUE(usd_per_eur)   OVER (PARTITION BY gf ORDER BY full_date)
           / {litres_per_barrel}, 4)                                       AS brent_eur_litre
FROM m;

-- National daily prices, with crude oil per litre alongside.
CREATE MATERIALIZED VIEW mart_daily_national AS
SELECT c.date_key, c.full_date, c.fuel_group, c.service,
       COUNT(*)                                                      AS prices,
       ROUND(AVG(c.price_eur), 4)                                    AS avg_price,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY c.price_eur)      AS median_price,
       MAX(mk.brent_eur_litre)                                       AS brent_eur_litre
FROM v_price_clean c
LEFT JOIN mart_market_daily mk USING (date_key)
GROUP BY c.date_key, c.full_date, c.fuel_group, c.service;

-- Monthly average by region (self-service, road stations).
CREATE MATERIALIZED VIEW mart_region_monthly AS
SELECT DATE_TRUNC('month', c.full_date)::date AS month, r.region, r.macro_area, c.fuel_group,
       COUNT(*)                     AS prices,
       ROUND(AVG(c.price_eur), 4)   AS avg_price
FROM v_price_clean c
JOIN dim_region r USING (province)
WHERE c.service = 'Self-service' AND c.station_type = 'Road'
GROUP BY 1, 2, 3, 4;

CREATE UNIQUE INDEX ON mart_daily_national (date_key, fuel_group, service);
CREATE UNIQUE INDEX ON mart_region_monthly (month, region, fuel_group);
