# tests/test_deploy_no_bom_or_crlf.py
#
# Guards against a regression that already happened once during this plan
# (Task 3): a UTF-8 BOM and/or CRLF line endings crept into deploy/*.sh.
# A BOM before the shebang breaks `#!/usr/bin/env bash`, and CRLF makes
# Linux fail with "bad interpreter: /usr/bin/env bash^M". The existing
# bash-syntax tests (test_deploy_setup_sh.py, test_deploy_provision_sh.py)
# silently *skip* rather than fail when no real bash is found on PATH, so
# on a Windows box without Git Bash this regression would slip by
# invisibly. These tests read the raw bytes directly and always run.
from pathlib import Path

DEPLOY_FILES = [
    Path("deploy/crypto-sim.service"),
    Path("deploy/crypto-sim-dashboard.service"),
    Path("deploy/setup.sh"),
    Path("deploy/provision-ct303.sh"),
]

BOM = b"\xef\xbb\xbf"


def test_deploy_files_have_no_utf8_bom():
    for path in DEPLOY_FILES:
        data = path.read_bytes()
        assert not data.startswith(BOM), f"{path} starts with a UTF-8 BOM"


def test_deploy_files_have_no_crlf_line_endings():
    for path in DEPLOY_FILES:
        data = path.read_bytes()
        assert b"\r" not in data, f"{path} contains CR bytes (CRLF line endings)"
