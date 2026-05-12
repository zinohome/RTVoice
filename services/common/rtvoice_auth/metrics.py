"""SP10 G3 — 跨 service 共享的 per-key Prometheus metric 定义。

每个 service 引入这 5 个 metric，统一 schema、统一 label set。Grafana 面板
按 `key_id` 切片即可获得 top-N、每 key 流量、每 key 资源消耗。

label 规范：
- `service`：rtvoice/<svc-name>（如 "stt-server"）
- `endpoint`：HTTP route 路径 / WS endpoint 标识（"/v1/asr"）
- `key_id`：来自 metrics_labels.safe_key_id；anonymous / internal / key_xxx / unknown_xxxxxxxx
- `status`：HTTP status code 字符串（"200" "401" ...）；WS 用 "ws_close.4001" 之类

5 个 metric 名固定（与 G3 done spec 对齐），不要在 service 内部 shadow。
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram


REQUESTS_TOTAL = Counter(
    "rtvoice_requests_total",
    "Total requests served (HTTP + WS) — labeled by service / endpoint / key_id / status",
    ["service", "endpoint", "key_id", "status"],
)

REQUEST_DURATION_SECONDS = Histogram(
    "rtvoice_request_duration_seconds",
    "Request handling time in seconds",
    ["service", "endpoint", "key_id"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

STT_AUDIO_SECONDS_TOTAL = Counter(
    "rtvoice_stt_audio_seconds_total",
    "Total audio seconds processed by STT (sum of all PCM frames in)",
    ["key_id"],
)

TTS_CHARS_TOTAL = Counter(
    "rtvoice_tts_chars_total",
    "Total characters synthesized by TTS",
    ["key_id"],
)

REALTIME_SESSION_DURATION_SECONDS = Histogram(
    "rtvoice_realtime_session_duration_seconds",
    "Realtime Voice session lifetime in seconds (create → cleanup)",
    ["key_id"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800),
)
