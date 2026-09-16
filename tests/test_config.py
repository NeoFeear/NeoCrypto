# tests/test_config.py
from decimal import Decimal
from pathlib import Path

from config import load_config


def test_load_config_parses_defaults(tmp_path: Path):
    sample = tmp_path / "config.yaml"
    sample.write_text(
        """
data_source: binance
db_path: crypto_sim.db
watchlist:
  - BTCUSDT
  - ETHUSDT
liquidity:
  min_quote_volume_24h: 50000000
  max_spread_bps: 10
backtest:
  initial_capital: 1000
  interval: 1h
  lookback_days: 90
fees:
  default_fee_pct: 0.001
live:
  poll_interval_seconds: 300
  poll_kline_interval: 5m
  active_symbol: BTCUSDT
  active_strategy: dca
snapshots:
  retention_detail_days: 30
strategy_defaults:
  buy_hold:
    invest_at: start
  dca:
    amount_per_buy: 50
    frequency_hours: 24
    reference_price: close
  grid:
    lower_bound: 25000
    upper_bound: 35000
    n_levels: 10
    spacing: geometric
    order_size_quote: 100
""",
        encoding="utf-8",
    )

    cfg = load_config(sample)

    assert cfg.data_source == "binance"
    assert cfg.db_path == "crypto_sim.db"
    assert cfg.watchlist == ["BTCUSDT", "ETHUSDT"]
    assert cfg.liquidity.min_quote_volume_24h == Decimal("50000000")
    assert cfg.liquidity.max_spread_bps == Decimal("10")
    assert cfg.backtest.initial_capital == Decimal("1000")
    assert cfg.backtest.interval == "1h"
    assert cfg.backtest.lookback_days == 90
    assert cfg.fees.default_fee_pct == Decimal("0.001")
    assert cfg.live.poll_interval_seconds == 300
    assert cfg.live.poll_kline_interval == "5m"
    assert cfg.live.active_symbol == "BTCUSDT"
    assert cfg.live.active_strategy == "dca"
    assert cfg.snapshots.retention_detail_days == 30
    assert cfg.strategy_defaults == {
        "buy_hold": {"invest_at": "start"},
        "dca": {"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"},
        "grid": {"lower_bound": 25000, "upper_bound": 35000, "n_levels": 10, "spacing": "geometric", "order_size_quote": 100},
    }
