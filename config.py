# config.py
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml


@dataclass(frozen=True)
class LiquidityConfig:
    min_quote_volume_24h: Decimal
    max_spread_bps: Decimal


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: Decimal
    interval: str
    lookback_days: int


@dataclass(frozen=True)
class FeesConfig:
    default_fee_pct: Decimal


@dataclass(frozen=True)
class LiveConfig:
    poll_interval_seconds: int
    poll_kline_interval: str


@dataclass(frozen=True)
class SnapshotsConfig:
    retention_detail_days: int


@dataclass(frozen=True)
class Config:
    data_source: str
    watchlist: list[str]
    liquidity: LiquidityConfig
    backtest: BacktestConfig
    fees: FeesConfig
    live: LiveConfig
    snapshots: SnapshotsConfig


def load_config(path: str | Path = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Config(
        data_source=raw["data_source"],
        watchlist=list(raw["watchlist"]),
        liquidity=LiquidityConfig(
            min_quote_volume_24h=Decimal(str(raw["liquidity"]["min_quote_volume_24h"])),
            max_spread_bps=Decimal(str(raw["liquidity"]["max_spread_bps"])),
        ),
        backtest=BacktestConfig(
            initial_capital=Decimal(str(raw["backtest"]["initial_capital"])),
            interval=raw["backtest"]["interval"],
            lookback_days=int(raw["backtest"]["lookback_days"]),
        ),
        fees=FeesConfig(default_fee_pct=Decimal(str(raw["fees"]["default_fee_pct"]))),
        live=LiveConfig(
            poll_interval_seconds=int(raw["live"]["poll_interval_seconds"]),
            poll_kline_interval=raw["live"]["poll_kline_interval"],
        ),
        snapshots=SnapshotsConfig(
            retention_detail_days=int(raw["snapshots"]["retention_detail_days"])
        ),
    )
