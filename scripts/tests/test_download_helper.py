"""Test scripts/download_model.sh via subprocess."""
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "download_model.sh"


def _wget_supports_file_scheme() -> bool:
    """Some GNU wget builds omit the file:// protocol handler."""
    try:
        r = subprocess.run(
            ["wget", "-q", "-O", "/dev/null", "file:///dev/null"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(
    not _wget_supports_file_scheme(),
    reason="local wget build lacks file:// protocol support",
)
def test_download_succeeds_for_large_file(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"\x00" * 10240)
    dest = tmp_path / "dest.bin"
    r = subprocess.run(["sh", str(SCRIPT), f"file://{src}", str(dest), "1024"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert dest.stat().st_size == 10240


def test_download_fails_when_too_small(tmp_path):
    src = tmp_path / "small.txt"
    src.write_text("x")
    dest = tmp_path / "dest.txt"
    r = subprocess.run(["sh", str(SCRIPT), f"file://{src}", str(dest), "100"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert not dest.exists() or dest.stat().st_size < 100
