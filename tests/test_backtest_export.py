import csv
from decimal import Decimal
from pathlib import Path

from backtest import write_csv


def test_write_csv_round_trips_rows(tmp_path: Path):
    rows = [
        {"symbol": "BTCUSDT", "strategy": "dca", "return_pct": Decimal("12.34"), "nb_trades": 2},
        {"symbol": "BTCUSDT", "strategy": "grid", "return_pct": Decimal("-5.00"), "nb_trades": 10},
    ]
    out_path = tmp_path / "report.csv"

    write_csv(rows, str(out_path))

    with open(out_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        read_rows = list(reader)

    assert len(read_rows) == 2
    assert read_rows[0]["symbol"] == "BTCUSDT"
    assert read_rows[0]["strategy"] == "dca"
    assert read_rows[0]["return_pct"] == "12.34"
    assert read_rows[1]["nb_trades"] == "10"


def test_write_csv_empty_rows_does_not_crash(tmp_path: Path):
    out_path = tmp_path / "empty.csv"
    write_csv([], str(out_path))
    assert out_path.exists()
    assert out_path.read_text(encoding="utf-8") == ""
