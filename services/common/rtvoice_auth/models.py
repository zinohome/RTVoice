"""Pydantic v2 Key model for multi-tenant auth."""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Key(BaseModel):
    """API key 元数据；secret 仅创建时返一次，store 只保存 sha256 hex。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    secret_hash: str
    name: str
    sessions_concurrent_max: int = Field(5, ge=1, le=10000)
    sessions_per_hour_max: int = Field(100, ge=1, le=1000000)
    scopes: list[str] = Field(default_factory=lambda: ["stt", "tts", "realtime", "tokens"])
    created_at: datetime
    revoked_at: Optional[datetime] = None
    notes: str = ""
    legacy: bool = False
