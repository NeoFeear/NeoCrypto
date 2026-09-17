from pathlib import Path

LIVE_UNIT_PATH = Path("deploy/crypto-sim.service")
DASHBOARD_UNIT_PATH = Path("deploy/crypto-sim-dashboard.service")
DASHBOARD_APP_PATH = Path("dashboard/app.py")


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


def test_live_engine_unit_sets_working_directory():
    # WorkingDirectory=/opt/crypto-sim is the single most load-bearing
    # directive in the deployment: config.yaml, the DB file, .env, and
    # backtest_report.csv are all resolved as relative paths against it.
    text = LIVE_UNIT_PATH.read_text(encoding="utf-8")
    assert "WorkingDirectory=/opt/crypto-sim" in text


def test_live_engine_unit_has_unbuffered_logging_and_syslog_identifier():
    text = LIVE_UNIT_PATH.read_text(encoding="utf-8")
    assert "Environment=PYTHONUNBUFFERED=1" in text
    assert "SyslogIdentifier=crypto-sim" in text


def test_dashboard_unit_has_hardening_directives_and_read_write_access():
    text = DASHBOARD_UNIT_PATH.read_text(encoding="utf-8")
    for directive in (
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "User=cryptosim",
        "ReadWritePaths=/opt/crypto-sim",
    ):
        assert directive in text
    # ProtectSystem=strict with no ReadWritePaths mounts /opt/crypto-sim
    # read-only, which breaks SQLite WAL reads: a reader must open the
    # `-shm` file read-write to take a read-mark, so a fully read-only
    # mount raises SQLITE_READONLY_CANTLOCK while the live engine is
    # writing. ReadWritePaths=/opt/crypto-sim is required here for the
    # dashboard to keep working -- it is not a second layer of read-only
    # hardening, that would just break the app.


def test_dashboard_app_enforces_read_only_db_access_itself():
    # The dashboard's actual read-only invariant lives in dashboard/app.py's
    # get_conn(), which opens the DB with `mode=ro` -- that is real defense.
    # The systemd unit does NOT also need to deny write access to enforce
    # this (see test above for why it can't, anyway).
    text = DASHBOARD_APP_PATH.read_text(encoding="utf-8")
    assert "mode=ro" in text


def test_dashboard_unit_has_unbuffered_logging_and_syslog_identifier():
    text = DASHBOARD_UNIT_PATH.read_text(encoding="utf-8")
    assert "Environment=PYTHONUNBUFFERED=1" in text
    assert "SyslogIdentifier=crypto-sim-dashboard" in text


def test_dashboard_unit_sets_working_directory():
    text = DASHBOARD_UNIT_PATH.read_text(encoding="utf-8")
    assert "WorkingDirectory=/opt/crypto-sim" in text


def test_dashboard_unit_runs_the_dashboard_module_and_enables_on_boot():
    text = DASHBOARD_UNIT_PATH.read_text(encoding="utf-8")
    assert "ExecStart=/opt/crypto-sim/.venv/bin/python -m dashboard.app" in text
    assert "WantedBy=multi-user.target" in text
