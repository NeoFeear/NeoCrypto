-- db/schema.sql
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
    price TEXT NOT NULL,
    quantity TEXT NOT NULL,
    fee_pct TEXT NOT NULL,
    fee_amount TEXT NOT NULL,
    total_cost TEXT NOT NULL,
    realized_pnl TEXT,
    cash_balance_after TEXT NOT NULL,
    strategy_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id_achat INTEGER NOT NULL REFERENCES trades(id),
    symbol TEXT NOT NULL,
    quantity_restante TEXT NOT NULL,
    prix_achat TEXT NOT NULL,
    timestamp_achat INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    cash_balance TEXT NOT NULL,
    position_value TEXT NOT NULL,
    total_value TEXT NOT NULL,
    unrealized_pnl TEXT NOT NULL,
    realized_pnl_cumule TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('dca', 'grid', 'buy_hold')),
    symbol TEXT NOT NULL,
    params TEXT NOT NULL,
    capital_initial TEXT NOT NULL,
    actif INTEGER NOT NULL DEFAULT 1,
    date_creation INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS engine_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
