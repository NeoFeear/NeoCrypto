# tests/test_deploy_provision_sh.py
import shutil
import subprocess
from pathlib import Path

import pytest

PROVISION_SH = Path("deploy/provision-ct303.sh")


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


def test_provision_sh_has_valid_bash_syntax():
    bash = _find_real_bash()
    if bash is None:
        pytest.skip("no working bash found on PATH (Windows WSL-stub bash.exe does not count)")
    result = subprocess.run([bash, "-n", str(PROVISION_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_provision_sh_uses_strict_mode():
    text = PROVISION_SH.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text


def test_provision_sh_is_idempotent_via_pct_status_check():
    text = PROVISION_SH.read_text(encoding="utf-8")
    assert "pct status" in text
    assert "exit 0" in text


def test_provision_sh_creates_unprivileged_container_with_ctid_303():
    text = PROVISION_SH.read_text(encoding="utf-8")
    assert "CTID=303" in text
    assert "--unprivileged 1" in text


def test_provision_sh_configures_boot_and_dhcp_networking():
    text = PROVISION_SH.read_text(encoding="utf-8")
    assert "--onboot 1" in text
    assert "ip=dhcp" in text


def test_provision_sh_configures_europe_paris_timezone():
    # The app is Europe/Paris-aware (ZoneInfo("Europe/Paris") for the
    # Discord daily summary); default CT timezone would otherwise be UTC,
    # which is purely a journald-readability annoyance but worth fixing.
    text = PROVISION_SH.read_text(encoding="utf-8")
    assert "--timezone Europe/Paris" in text


def test_provision_sh_starts_an_existing_but_stopped_container_before_exiting():
    # A partial first run (created but failed to start) or a manual stop
    # must not leave the idempotency guard reporting success while CT303
    # is actually stopped -- that breaks Task 6's later `pct exec` steps
    # with a confusing error on what looks like a successful provision.
    text = PROVISION_SH.read_text(encoding="utf-8")
    assert "pct start \"$CTID\"" in text
    # The start-if-needed logic must be inside the existing-container
    # early-exit branch, not merely present anywhere in the file.
    guard_start = text.index('if pct status "$CTID"')
    guard_end = text.index("exit 0", guard_start)
    guard_block = text[guard_start:guard_end]
    assert "pct start" in guard_block
    assert "running" in guard_block
