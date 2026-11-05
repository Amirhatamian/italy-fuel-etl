# Shortcuts for a local PostgreSQL. Override with: make backfill DATABASE_URL=postgresql://...
export DATABASE_URL ?= postgresql:///fuel_dw

init:      ; python -m etl init-db && python -m etl load-registry --file data/registry/anagrafica_impianti_attivi_20261102.csv
backfill:  ; python -m etl backfill --from 2025-4 --to 2026-3
market:    ; python -m etl load-market --start 2025-09-01 --end 2026-11-02
marts:     ; python -m etl refresh-marts
check:     ; python -m etl check
daily:     ; python -m etl run-daily
all: init backfill market marts check
test:      ; python -m pytest -q
analysis:  ; python analysis/rockets_and_feathers.py
.PHONY: init backfill market marts check daily all test analysis
