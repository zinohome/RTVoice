"""pick_bearer_subprotocol 单元测试。"""
from rtvoice_auth.ws import pick_bearer_subprotocol


class _FakeWS:
    def __init__(self, header_value: str = "") -> None:
        self.headers = {"sec-websocket-protocol": header_value} if header_value else {}


def test_pick_bearer_subprotocol_single():
    ws = _FakeWS("bearer.abc123")
    assert pick_bearer_subprotocol(ws) == "bearer.abc123"


def test_pick_bearer_subprotocol_first_when_multiple():
    ws = _FakeWS("bearer.abc, bearer.def")
    assert pick_bearer_subprotocol(ws) == "bearer.abc"


def test_pick_bearer_subprotocol_skips_other_protocols():
    ws = _FakeWS("rtvoice, bearer.token, json")
    assert pick_bearer_subprotocol(ws) == "bearer.token"


def test_pick_bearer_subprotocol_returns_none_when_no_bearer():
    ws = _FakeWS("rtvoice, json")
    assert pick_bearer_subprotocol(ws) is None


def test_pick_bearer_subprotocol_returns_none_when_header_missing():
    ws = _FakeWS()
    assert pick_bearer_subprotocol(ws) is None


def test_pick_bearer_subprotocol_handles_whitespace():
    ws = _FakeWS("  bearer.with-padding  ")
    assert pick_bearer_subprotocol(ws) == "bearer.with-padding"
