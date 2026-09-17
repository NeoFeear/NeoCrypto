from pathlib import Path

LIVE_UNIT_PATH = Path("deploy/crypto-sim.service")
DASHBOARD_UNIT_PATH = Path("deploy/crypto-sim-dashboard.service")


def test_live_engine_unit_has_required_hardening_directives():
    text = LIVE_UNIT_PATH.read_text(encoding="utf-8")
    for directive in ("NoNewPrivileges=true", "ProtectSystem=strict", "ReadWritePaths=/opt/crypto-sim"):
        assert directive in text


def test_live_engine_unit_runs_as_dedicated_nonroot_user():
    text = LIVE_UNIT_PATH.read_text(encoding="utf-8")
    assert "User=cryptosim" in text
    assert "User=root" not in text


def test_live_engine_unit_restarts_on_failure_and_enables_on_boot():
    text = LIVE_UNIT_PATH.read_text(encoding="utf-8")
    assert "Restart=on-failure" in text
    assert "WantedBy=multi-user.target" in text
    assert "ExecStart=/opt/crypto-sim/.venv/bin/python /opt/crypto-sim/live_engine.py" in text


def test_dashboard_unit_has_hardening_directives_but_no_write_access():
    text = DASHBOARD_UNIT_PATH.read_text(encoding="utf-8")
    for directive in ("NoNewPrivileges=true", "ProtectSystem=strict", "User=cryptosim"):
        assert directive in text
    # The dashboard never writes anything (Plan 5's get_conn() is mode=ro,
    # no log files) -- omitting ReadWritePaths entirely leaves its whole
    # filesystem read-only under ProtectSystem=strict, enforcing the
    # read-only invariant a second time at the systemd level.
    assert "ReadWritePaths=" not in text


def test_dashboard_unit_runs_the_dashboard_module_and_enables_on_boot():
    text = DASHBOARD_UNIT_PATH.read_text(encoding="utf-8")
    assert "ExecStart=/opt/crypto-sim/.venv/bin/python -m dashboard.app" in text
    assert "WantedBy=multi-user.target" in text
