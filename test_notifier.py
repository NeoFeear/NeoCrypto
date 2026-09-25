# test_notifier.py
"""Sends one example of each Discord notification type with fake data, for
visual validation independent of the trading engine. Run manually after
filling in .env, before starting live_engine.py for the first time:

    python test_notifier.py
"""
from decimal import Decimal

from discord_notifier import PairDailySummary, load_discord_webhooks, send_alert, send_log, send_portfolio_daily_summary, send_transaction
from engine.fifo_engine import Side, Trade


def main() -> None:
    webhooks = load_discord_webhooks()
    example_initial_cash = Decimal("125")  # live.capital_per_pair, e.g. 1000 / 8 pairs

    print("Envoi d'un exemple de transaction (BUY)...")
    send_transaction(webhooks.transactions, Trade(
        id=1, timestamp=1_700_000_000_000, symbol="BTCUSDT", side=Side.BUY,
        price=Decimal("50000"), quantity=Decimal("0.01"), fee_pct=Decimal("0.001"),
        fee_amount=Decimal("0.5"), total_cost=Decimal("50.05"), realized_pnl=None,
        cash_balance_after=Decimal("74.95"), strategy_name="dca",
    ), initial_cash=example_initial_cash)

    print("Envoi d'un exemple de transaction (SELL, gain)...")
    send_transaction(webhooks.transactions, Trade(
        id=2, timestamp=1_700_000_300_000, symbol="BTCUSDT", side=Side.SELL,
        price=Decimal("52000"), quantity=Decimal("0.01"), fee_pct=Decimal("0.001"),
        fee_amount=Decimal("0.52"), total_cost=Decimal("519.48"), realized_pnl=Decimal("18.98"),
        cash_balance_after=Decimal("94.43"), strategy_name="dca",
    ), initial_cash=example_initial_cash)

    print("Envoi d'un exemple de resume quotidien (portefeuille global)...")
    send_portfolio_daily_summary(
        webhooks.daily_summary,
        pairs=[
            PairDailySummary(
                symbol="BTCUSDT", total_value=Decimal("131.25"),
                return_pct_since_start=Decimal("5.00"), return_pct_24h=Decimal("1.25"),
            ),
            PairDailySummary(
                symbol="ETHUSDT", total_value=Decimal("119.40"),
                return_pct_since_start=Decimal("-4.48"), return_pct_24h=None,
            ),
        ],
        total_value=Decimal("250.65"), total_capital=Decimal("250"),
        return_pct=Decimal("0.26"), return_pct_24h=Decimal("0.5"), existing_message_id=None,
    )

    print("Envoi d'un exemple d'alerte critique...")
    send_alert(webhooks.alerts, "api_error", "Exemple: 3 echecs API consecutifs pour BTCUSDT", severity="critical")

    print("Envoi d'un exemple de log technique...")
    send_log(webhooks.logs, "Exemple: moteur live demarre pour BTCUSDT/dca", level="INFO")

    print("Termine. Verifie les 4 salons Discord.")


if __name__ == "__main__":
    main()
