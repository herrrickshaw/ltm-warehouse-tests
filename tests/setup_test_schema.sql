-- Mirrors the production optimized schema (ltm_symbols, ltm_fundamentals_fact,
-- ltm_fundamentals view) so the analysis/backfill scripts can be tested here
-- before running against the real warehouse.

CREATE TABLE ltm_symbols (
    symbol_id SERIAL PRIMARY KEY,
    symbol TEXT UNIQUE NOT NULL
);

CREATE TABLE ltm_fundamentals_fact (
    country CHAR(2) NOT NULL,
    symbol_id INT4 NOT NULL REFERENCES ltm_symbols(symbol_id),
    dt DATE NOT NULL,
    open_p REAL,
    high_p REAL,
    low_p REAL,
    close_p REAL,
    volume INT8,
    PRIMARY KEY (country, symbol_id, dt)
);

CREATE VIEW ltm_fundamentals AS
SELECT f.country, s.symbol AS "Symbol", f.dt AS "Date",
       f.open_p AS "Open", f.high_p AS "High", f.low_p AS "Low",
       f.close_p AS "Close", f.volume AS "Volume"
FROM ltm_fundamentals_fact f
JOIN ltm_symbols s ON s.symbol_id = f.symbol_id;
