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
