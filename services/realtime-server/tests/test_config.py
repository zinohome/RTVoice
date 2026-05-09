"""Test config loads env vars with correct defaults."""
import os


def test_defaults_when_no_env(monkeypatch):
    """When no env set, defaults apply (RTX 3060 调优)."""
    for k in [
        "RTVOICE_MAX_CONCURRENT_SESSIONS",
        "RTVOICE_SESSION_QUEUE_DEPTH",
        "RTVOICE_SESSION_CREATE_TIMEOUT_S",
        "RTVOICE_SESSION_IDLE_TIMEOUT_S",
        "RTVOICE_SESSION_MAX_LIFETIME_S",
        "RTVOICE_WS_DISCONNECT_GRACE_S",
        "RTVOICE_TURN_TIMEOUT_S",
    ]:
        monkeypatch.delenv(k, raising=False)
    # force re-import
    import importlib
    if "app.config" in __import__("sys").modules:
        importlib.reload(__import__("sys").modules["app.config"])
    from app import config
    assert config.MAX_CONCURRENT_SESSIONS == 5
    assert config.SESSION_QUEUE_DEPTH == 0
    assert config.SESSION_CREATE_TIMEOUT_S == 60
    assert config.SESSION_IDLE_TIMEOUT_S == 30
    assert config.SESSION_MAX_LIFETIME_S == 1800
    assert config.WS_DISCONNECT_GRACE_S == 0
    assert config.TURN_TIMEOUT_S == 60


def test_env_override(monkeypatch):
    """Env vars override defaults (24GB GPU upgrade scenario)."""
    monkeypatch.setenv("RTVOICE_MAX_CONCURRENT_SESSIONS", "10")
    monkeypatch.setenv("RTVOICE_SESSION_IDLE_TIMEOUT_S", "60")
    import importlib
    if "app.config" in __import__("sys").modules:
        importlib.reload(__import__("sys").modules["app.config"])
    from app import config
    assert config.MAX_CONCURRENT_SESSIONS == 10
    assert config.SESSION_IDLE_TIMEOUT_S == 60


def test_sp3_defaults(monkeypatch):
    """SP3 新增 env vars 默认值（RTX 3060 调优）"""
    for k in [
        "RTVOICE_MEMORY_MAX_TURNS",
        "RTVOICE_DEFAULT_PROMPT",
        "RTVOICE_AUDIT_DIR",
        "RTVOICE_AUDIT_QUEUE_MAX",
        "RTVOICE_PROMPT_MAX_CHARS",
    ]:
        monkeypatch.delenv(k, raising=False)
    import importlib, sys
    if "app.config" in sys.modules:
        importlib.reload(sys.modules["app.config"])
    from app import config
    assert config.MEMORY_MAX_TURNS == 6
    assert config.DEFAULT_PROMPT == "你是语音助手。用中文简短回答（≤2 句）。"
    assert config.AUDIT_DIR == "/data/transcripts"
    assert config.AUDIT_QUEUE_MAX == 1000
    assert config.PROMPT_MAX_CHARS == 2000
