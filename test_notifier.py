# test_notifier.py
"""Sends one example of each Discord notification type with fake data, for
visual validation independent of the trading engine. Run manually after
filling in .env, before starting live_engine.py for the first time:

    python test_notifier.py
"""
from decimal import Decimal

from discord_notifier import load_discord_webhooks, send_alert, send_daily_summary, send_log, send_transaction
from engine.fifo_engine import Side, Trade


def main() -> None:
    webhooks = load_discord_webhooks()

    print("Envoi d'un exemple de transaction (BUY)...")
    send_transaction(webhooks.transactions, Trade(
        id=1, timestamp=1_700_000_000_000, symbol="BTCUSDT", side=Side.BUY,
        price=Decimal("50000"), quantity=Decimal("0.01"), fee_pct=Decimal("0.001"),
        fee_amount=Decimal("0.5"), total_cost=Decimal("500.5"), realized_pnl=None,
        cash_balance_after=Decimal("499.5"), strategy_name="dca",
    ))

    print("Envoi d'un exemple de transaction (SELL, gain)...")
    send_transaction(webhooks.transactions, Trade(
        id=2, timestamp=1_700_000_300_000, symbol="BTCUSDT", side=Side.SELL,
        price=Decimal("52000"), quantity=Decimal("0.01"), fee_pct=Decimal("0.001"),
        fee_amount=Decimal("0.52"), total_cost=Decimal("519.48"), realized_pnl=Decimal("18.98"),
        cash_balance_after=Decimal("1019"), strategy_name="dca",
    ))

    print("Envoi d'un exemple de resume quotidien...")
    send_daily_summary(
        webhooks.daily_summary, symbol="BTCUSDT", total_value=Decimal("1050.25"),
        realized_pnl_cumule=Decimal("30.10"), unrealized_pnl=Decimal("20.15"),
        return_pct=Decimal("5.025"), existing_message_id=None,
    )

    print("Envoi d'un exemple d'alerte critique...")
    send_alert(webhooks.alerts, "api_error", "Exemple: 3 echecs API consecutifs pour BTCUSDT", severity="critical")

    print("Envoi d'un exemple de log technique...")
    send_log(webhooks.logs, "Exemple: moteur live demarre pour BTCUSDT/dca", level="INFO")

    print("Termine. Verifie les 4 salons Discord.")


if __name__ == "__main__":
    main()
