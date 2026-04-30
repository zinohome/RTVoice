"""agent-worker Prometheus 指标定义 + HTTP 暴露。

agent 不是 HTTP server，通过 prometheus_client 自带的 wsgi server 在独立端口
（默认 9100）暴露 /metrics。

主要指标：
    rtvoice_agent_state{state}              当前 FSM 状态（5 种之一）— Gauge
    rtvoice_agent_rounds_total              完成的对话轮数 — Counter
    rtvoice_agent_round_seconds             每轮总耗时 — Histogram
    rtvoice_agent_round_phrases             每轮 phrase 数 — Histogram
    rtvoice_agent_first_audio_seconds       用户说完到首字音频送出 — Histogram
    rtvoice_agent_barge_ins_total           barge-in 触发次数 — Counter
    rtvoice_agent_pipeline_errors_total     pipeline 异常次数 — Counter
    rtvoice_agent_stt_finals_total          STT final 触发次数 — Counter
    rtvoice_agent_stt_partials_total        STT partial 触发次数 — Counter
"""

from __future__ import annotations

import logging
import os

from prometheus_client import Counter, Gauge, Histogram, start_http_server

log = logging.getLogger("rtvoice.agent.metrics")


METRICS_PORT = int(os.environ.get("METRICS_PORT", "9100"))


# ---------- Gauges ----------
AGENT_STATE = Gauge(
    "rtvoice_agent_state",
    "Current FSM state (1=active, 0=inactive)",
    ["state"],
)

# ---------- Counters ----------
ROUNDS_TOTAL = Counter("rtvoice_agent_rounds_total", "Total dialog rounds completed")
BARGE_INS_TOTAL = Counter("rtvoice_agent_barge_ins_total", "Barge-in events")
PIPELINE_ERRORS_TOTAL = Counter("rtvoice_agent_pipeline_errors_total", "Pipeline exceptions")
STT_FINALS_TOTAL = Counter("rtvoice_agent_stt_finals_total", "STT final events received")
STT_PARTIALS_TOTAL = Counter("rtvoice_agent_stt_partials_total", "STT partial events received")

# ---------- Histograms ----------
ROUND_SECONDS = Histogram(
    "rtvoice_agent_round_seconds",
    "End-to-end round duration (user speech_end → agent speech_end)",
    buckets=(0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 20.0, 60.0),
)
ROUND_PHRASES = Histogram(
    "rtvoice_agent_round_phrases",
    "Phrases per round (LLM split)",
    buckets=(1, 2, 3, 5, 10, 20),
)
FIRST_AUDIO_SECONDS = Histogram(
    "rtvoice_agent_first_audio_seconds",
    "Time from STT final to first TTS audio frame published (TTFB perceived)",
    buckets=(0.2, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0),
)


def init_state_gauge() -> None:
    """初始化所有 state 标签为 0；transition 时只设置当前为 1，其余为 0。"""
    for s in ("idle", "listening", "thinking", "speaking", "interrupted"):
        AGENT_STATE.labels(state=s).set(0)
    AGENT_STATE.labels(state="idle").set(1)


def set_state(state_name: str) -> None:
    for s in ("idle", "listening", "thinking", "speaking", "interrupted"):
        AGENT_STATE.labels(state=s).set(1 if s == state_name else 0)


def start_metrics_server() -> None:
    """在独立端口启 wsgi server 暴露 /metrics。"""
    log.info("Prometheus metrics 监听 :%d/metrics", METRICS_PORT)
    start_http_server(METRICS_PORT)
