"""SP10 G3 — safe_key_id / hash_label 单测。"""
from rtvoice_auth.metrics_labels import safe_key_id, hash_label, ANONYMOUS, INTERNAL


class _FakeKey:
    def __init__(self, id: str) -> None:
        self.id = id


def test_safe_key_id_from_key_object():
    assert safe_key_id(_FakeKey("key_abc123")) == "key_abc123"


def test_safe_key_id_none_returns_anonymous():
    assert safe_key_id(None) == ANONYMOUS


def test_safe_key_id_empty_str_returns_anonymous():
    assert safe_key_id("") == ANONYMOUS


def test_safe_key_id_anonymous_literal():
    assert safe_key_id(ANONYMOUS) == ANONYMOUS


def test_safe_key_id_internal_literal():
    assert safe_key_id(INTERNAL) == INTERNAL


def test_safe_key_id_legitimate_key_string():
    assert safe_key_id("key_abc12345") == "key_abc12345"


def test_safe_key_id_unknown_string_hashed():
    out = safe_key_id("some-random-thing-not-a-key")
    assert out.startswith("unknown_") and len(out) == len("unknown_") + 8


def test_safe_key_id_key_no_id_attr():
    class NoIdKey:
        pass
    assert safe_key_id(NoIdKey()) == ANONYMOUS


def test_hash_label_stable():
    a = hash_label("room-foo")
    b = hash_label("room-foo")
    c = hash_label("room-bar")
    assert a == b
    assert a != c
    assert len(a) == 8


def test_hash_label_empty():
    assert hash_label("") == "empty"
    assert hash_label("", prefix="r_") == "r_empty"


def test_hash_label_prefix():
    out = hash_label("room-x", prefix="r_")
    assert out.startswith("r_") and len(out) == len("r_") + 8
