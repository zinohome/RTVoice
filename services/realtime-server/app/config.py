"""Centralized env-driven config for realtime-server.

All scaling / lifecycle parameters here. Future GPU upgrades only require
.env changes, no code changes (per spec D-2026-05-08-A.2).
"""
import os


def _int(key: str, default: int) -> int:
    return int(os.environ.get(key, str(default)))


def _float(key: str, default: float) -> float:
    return float(os.environ.get(key, str(default)))


def _str(key: str, default: str) -> str:
    return os.environ.get(key, default)


# Service URLs (resolved from agent-worker pattern)
STT_WS_URL = _str("STT_WS_URL", "ws://stt-server:9090/v1/asr")
LLM_BASE_URL = _str("LLM_BASE_URL", "http://llm-server:11434/v1")
LLM_MODEL = _str("LLM_MODEL", "qwen2.5:1.5b")
LLM_API_KEY = _str("LLM_API_KEY", "ollama")
TTS_BASE_URL = _str("TTS_BASE_URL", "http://tts-server:9880")
TOKEN_BASE_URL = _str("TOKEN_BASE_URL", "http://token-server:8000")

# Auth
RTVOICE_API_KEY = _str("RTVOICE_API_KEY", "").strip()  # empty = dev mode no auth
# tts-server 独有的高权限 key（音色注册/删除）；Admin Console 代理 /v1/console/voices 时注入
TTS_ADMIN_API_KEY = _str("TTS_ADMIN_API_KEY", "").strip()

# Public WS URL base (returned in POST /v1/sessions response)
PUBLIC_WS_BASE = _str("PUBLIC_WS_BASE", "ws://realtime-server:9000")

# Concurrency / lifecycle (RTX 3060 12GB tuned defaults)
MAX_CONCURRENT_SESSIONS = _int("RTVOICE_MAX_CONCURRENT_SESSIONS", 5)
SESSION_QUEUE_DEPTH = _int("RTVOICE_SESSION_QUEUE_DEPTH", 0)
SESSION_CREATE_TIMEOUT_S = _int("RTVOICE_SESSION_CREATE_TIMEOUT_S", 60)
SESSION_IDLE_TIMEOUT_S = _int("RTVOICE_SESSION_IDLE_TIMEOUT_S", 30)
SESSION_MAX_LIFETIME_S = _int("RTVOICE_SESSION_MAX_LIFETIME_S", 1800)
WS_DISCONNECT_GRACE_S = _int("RTVOICE_WS_DISCONNECT_GRACE_S", 0)
TURN_TIMEOUT_S = _int("RTVOICE_TURN_TIMEOUT_S", 60)

# TTS / LLM scaling (forward-compat hooks; v1 not yet acted upon)
TTS_MODEL_REPLICAS = _int("RTVOICE_TTS_MODEL_REPLICAS", 1)
LLM_MAX_CONCURRENT = _int("RTVOICE_LLM_MAX_CONCURRENT", 4)

# STT timeout (turn 内等 STT final 的最长时间)
STT_FINAL_TIMEOUT_S = _float("STT_FINAL_TIMEOUT_S", 5.0)

# SP3 — Memory + Prompt + Audit
MEMORY_MAX_TURNS = _int("RTVOICE_MEMORY_MAX_TURNS", 6)
DEFAULT_PROMPT = _str("RTVOICE_DEFAULT_PROMPT", "你是语音助手。用中文简短回答（≤2 句）。")
AUDIT_DIR = _str("RTVOICE_AUDIT_DIR", "/data/transcripts")
AUDIT_QUEUE_MAX = _int("RTVOICE_AUDIT_QUEUE_MAX", 1000)
PROMPT_MAX_CHARS = _int("RTVOICE_PROMPT_MAX_CHARS", 2000)

# Voice defaults
DEFAULT_VOICE = _str("TTS_VOICE", "default_zh_female")
DEFAULT_LANG = _str("TTS_LANG", "cmn")

# Logging
LOG_LEVEL = _str("LOG_LEVEL", "INFO").upper()


def log_summary(logger):
    """启动时打印实际生效的参数（便于排障）"""
    logger.info("=== realtime-server config ===")
    logger.info("STT_WS_URL=%s LLM=%s TTS=%s", STT_WS_URL, LLM_MODEL, TTS_BASE_URL)
    logger.info("MAX_CONCURRENT_SESSIONS=%d QUEUE_DEPTH=%d",
                MAX_CONCURRENT_SESSIONS, SESSION_QUEUE_DEPTH)
    logger.info("CREATE_TIMEOUT=%ds IDLE_TIMEOUT=%ds MAX_LIFETIME=%ds DISCONNECT_GRACE=%ds TURN_TIMEOUT=%ds",
                SESSION_CREATE_TIMEOUT_S, SESSION_IDLE_TIMEOUT_S,
                SESSION_MAX_LIFETIME_S, WS_DISCONNECT_GRACE_S, TURN_TIMEOUT_S)
    logger.info("TTS_REPLICAS=%d LLM_MAX_CONCURRENT=%d",
                TTS_MODEL_REPLICAS, LLM_MAX_CONCURRENT)
    logger.info("auth=%s", "enabled" if RTVOICE_API_KEY else "disabled (dev mode)")
    logger.info("SP3: MEMORY_MAX_TURNS=%d AUDIT_DIR=%s PROMPT_MAX_CHARS=%d",
                MEMORY_MAX_TURNS, AUDIT_DIR, PROMPT_MAX_CHARS)
