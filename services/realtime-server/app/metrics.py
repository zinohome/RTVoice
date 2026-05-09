"""SP4 A-lite: realtime-server 自定义 Prometheus metrics."""
from prometheus_client import Counter, Gauge, REGISTRY

# Handle re-import in tests by checking if metrics already exist
_SESSIONS_ACTIVE = None
_TURNS_TOTAL = None
_AUDIT_QUEUE_DEPTH = None


def _ensure_metrics():
    """Lazily init metrics only once; idempotent for tests."""
    global _SESSIONS_ACTIVE, _TURNS_TOTAL, _AUDIT_QUEUE_DEPTH
    if _SESSIONS_ACTIVE is not None:
        return

    try:
        _SESSIONS_ACTIVE = Gauge(
            "rtvoice_realtime_sessions_active",
            "current number of active sessions",
        )
    except ValueError:
        # Already registered in this process
        _SESSIONS_ACTIVE = REGISTRY._names_to_collectors.get(
            "rtvoice_realtime_sessions_active"
        )

    try:
        _TURNS_TOTAL = Counter(
            "rtvoice_realtime_turns_total",
            "total run_turn invocations",
            ["status"],
        )
    except ValueError:
        _TURNS_TOTAL = REGISTRY._names_to_collectors.get(
            "rtvoice_realtime_turns_total"
        )

    try:
        _AUDIT_QUEUE_DEPTH = Gauge(
            "rtvoice_realtime_audit_queue_depth",
            "sum of all session audit queue sizes (no per-session label to avoid cardinality blowup)",
        )
    except ValueError:
        _AUDIT_QUEUE_DEPTH = REGISTRY._names_to_collectors.get(
            "rtvoice_realtime_audit_queue_depth"
        )


_ensure_metrics()

SESSIONS_ACTIVE = _SESSIONS_ACTIVE
TURNS_TOTAL = _TURNS_TOTAL
AUDIT_QUEUE_DEPTH = _AUDIT_QUEUE_DEPTH
