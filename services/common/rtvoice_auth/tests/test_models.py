"""Test Key Pydantic model."""
import pytest
from datetime import datetime, timezone


def test_key_minimal_construction():
    from rtvoice_auth.models import Key
    k = Key(
        id="key_test123",
        secret_hash="abc",
        name="test",
        created_at=datetime.now(timezone.utc),
    )
    assert k.id == "key_test123"
    assert k.sessions_concurrent_max == 5
    assert k.sessions_per_hour_max == 100
    assert k.scopes == ["stt", "tts", "realtime", "tokens"]
    assert k.revoked_at is None
    assert k.legacy is False


def test_key_round_trip_yaml_dict():
    from rtvoice_auth.models import Key
    k = Key(
        id="key_x", secret_hash="h", name="n",
        sessions_concurrent_max=10, sessions_per_hour_max=200,
        scopes=["stt", "tts"],
        created_at=datetime(2026, 5, 10, 8, tzinfo=timezone.utc),
        legacy=True,
    )
    d = k.model_dump(mode="json")
    assert d["id"] == "key_x"
    assert d["legacy"] is True
    k2 = Key.model_validate(d)
    assert k2.id == k.id
    assert k2.legacy is True


def test_key_revoked_at_optional():
    from rtvoice_auth.models import Key
    now = datetime.now(timezone.utc)
    k = Key(id="k", secret_hash="h", name="n", created_at=now, revoked_at=now)
    assert k.revoked_at is not None
