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
class LivePair:
    symbol: str
    strategy: str


@dataclass(frozen=True)
class LiveConfig:
    poll_interval_seconds: int
    poll_kline_interval: str
    pairs: list[LivePair]
    # Total live paper-trading budget, split EQUALLY across len(pairs) --
    # separate from backtest.initial_capital, which stays the FULL amount
    # per symbol for backtest.py's own comparative analysis (comparing
    # strategies needs each one to start from the same capital, which is a
    # different question from "how much real budget would this deployment
    # actually use"). See LiveConfig.capital_per_pair.
    total_capital: Decimal

    @property
    def capital_per_pair(self) -> Decimal:
        return self.total_capital / Decimal(len(self.pairs))


@dataclass(frozen=True)
class SnapshotsConfig:
    retention_detail_days: int


@dataclass(frozen=True)
class DiscordConfig:
    alert_drawdown_threshold_pct: Decimal


@dataclass(frozen=True)
class DashboardConfig:
    port: int


@dataclass(frozen=True)
class Config:
    data_source: str
    db_path: str
    watchlist: list[str]
    liquidity: LiquidityConfig
    backtest: BacktestConfig
    fees: FeesConfig
    live: LiveConfig
    snapshots: SnapshotsConfig
    discord: DiscordConfig
    dashboard: DashboardConfig
    strategy_defaults: dict


def load_config(path: str | Path = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Config(
        data_source=raw["data_source"],
        db_path=raw["db_path"],
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
            pairs=[LivePair(symbol=p["symbol"], strategy=p["strategy"]) for p in raw["live"]["pairs"]],
            total_capital=Decimal(str(raw["live"]["total_capital"])),
        ),
        snapshots=SnapshotsConfig(
            retention_detail_days=int(raw["snapshots"]["retention_detail_days"])
        ),
        discord=DiscordConfig(
            alert_drawdown_threshold_pct=Decimal(str(raw["discord"]["alert_drawdown_threshold_pct"]))
        ),
        dashboard=DashboardConfig(port=int(raw["dashboard"]["port"])),
        strategy_defaults=dict(raw["strategy_defaults"]),
    )
