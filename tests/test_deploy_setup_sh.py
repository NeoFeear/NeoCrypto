import shutil
import subprocess
from pathlib import Path

import pytest

SETUP_SH = Path("deploy/setup.sh")


def _find_real_bash() -> str | None:
    """On Windows, `bash` may resolve to the WSL launcher stub in
    System32, which prints a WSL-install message and exits 1 instead
    of running bash -- distinguish that from a real bash.exe (Git
    Bash or an actual WSL bash) by actually running it."""
    candidates = [shutil.which("bash")]
    candidates += [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        probe = subprocess.run([candidate, "-c", "echo ok"], capture_output=True, text=True)
        if probe.returncode == 0 and "ok" in probe.stdout:
            return candidate
    return None


def test_setup_sh_has_valid_bash_syntax():
    bash = _find_real_bash()
    if bash is None:
        pytest.skip("no working bash found on PATH (Windows WSL-stub bash.exe does not count)")
    result = subprocess.run([bash, "-n", str(SETUP_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_setup_sh_uses_strict_mode():
    text = SETUP_SH.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text


def test_setup_sh_refuses_to_run_as_non_root():
    text = SETUP_SH.read_text(encoding="utf-8")
    assert '"$(id -u)" -ne 0' in text


def test_setup_sh_creates_user_idempotently():
    text = SETUP_SH.read_text(encoding="utf-8")
    assert "id -u" in text and "useradd" in text


def test_setup_sh_installs_and_enables_both_systemd_units():
    text = SETUP_SH.read_text(encoding="utf-8")
    assert "crypto-sim.service" in text
    assert "crypto-sim-dashboard.service" in text
    assert "systemctl daemon-reload" in text
    assert "systemctl enable --now crypto-sim.service" in text
    assert "systemctl enable --now crypto-sim-dashboard.service" in text


def test_setup_sh_initializes_the_database_schema():
    text = SETUP_SH.read_text(encoding="utf-8")
    assert "init_db" in text


def test_setup_sh_initializes_the_db_path_from_config_not_hardcoded():
    # Plan's own Global Constraint: paths stay in config.yaml/deploy/*,
    # never hardcoded. Reading db_path via config.load_config() means a
    # future change to config.yaml's db_path can't silently init the
    # wrong file.
    text = SETUP_SH.read_text(encoding="utf-8")
    assert "load_config" in text
    assert "init_db(load_config().db_path)" in text
    assert "init_db('crypto_sim.db')" not in text


def test_setup_sh_does_not_depend_on_sudo():
    # sudo is not guaranteed present in the Debian 13 LXC template and is
    # not in the apt install list -- if absent, `set -e` aborts
    # mid-provisioning. runuser (util-linux) is always present on Debian.
    text = SETUP_SH.read_text(encoding="utf-8")
    assert "sudo " not in text
    assert 'runuser -u "$APP_USER" --' in text


def test_setup_sh_uses_noninteractive_apt_frontend():
    # pct exec allocates a tty, so a conffile prompt on `apt-get install`
    # would otherwise hang with no timeout.
    text = SETUP_SH.read_text(encoding="utf-8")
    assert "DEBIAN_FRONTEND=noninteractive" in text


def test_setup_sh_restricts_env_file_permissions():
    text = SETUP_SH.read_text(encoding="utf-8")
    assert 'chmod 600 "$APP_DIR/.env"' in text


def test_setup_sh_warns_that_rerunning_does_not_restart_active_services():
    # systemctl enable --now is a no-op on an already-active service, so a
    # setup.sh re-run after a code update silently leaves stale code
    # running unless the operator restarts services manually.
    text = SETUP_SH.read_text(encoding="utf-8")
    assert "systemctl restart crypto-sim.service crypto-sim-dashboard.service" in text
