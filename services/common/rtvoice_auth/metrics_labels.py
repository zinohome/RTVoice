"""SP10 G3 — per-key metric label 规范化 + 基数控制。

设计原则：
- 已鉴权 → key.id（不是 secret；防泄漏）
- 未鉴权 / health probe → "anonymous"
- 内部探针（agent-worker 等）→ "internal"
- 任何 unknown id → "unknown_<hash8>"（防 unbounded 基数爆炸）
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

ANONYMOUS = "anonymous"
INTERNAL = "internal"


def safe_key_id(key: Optional[Any]) -> str:
    """从 Key 对象 / None / 字符串归一化出 prometheus 安全的 label value。

    - Key 对象（含 .id 属性） → key.id
    - None / "" → "anonymous"
    - "internal" 字面 → "internal"
    - 其他字符串 → 走 unknown_<hash8>
    """
    if key is None:
        return ANONYMOUS
    if isinstance(key, str):
        if key == "":
            return ANONYMOUS
        if key in (ANONYMOUS, INTERNAL):
            return key
        if key.startswith("key_") and len(key) <= 32:
            return key  # 看起来像合法 Key.id
        return _unknown_hash(key)
    # Key model 实例
    kid = getattr(key, "id", None)
    if not kid:
        return ANONYMOUS
    return str(kid)


def _unknown_hash(s: str) -> str:
    """限制 unbounded 输入的基数：每个独特输入 → 1 个 unknown_xxxxxxxx label。"""
    h = hashlib.sha256(s.encode()).hexdigest()[:8]
    return f"unknown_{h}"


def hash_label(s: str, prefix: str = "") -> str:
    """通用 8-char hash，用于把自由文本 label（如 room 名）压成有界 label。

    SP10 T8 治 D3-S3：rtvoice_tokens_issued_total{room=...} 自由文本基数爆炸。
    """
    if not s:
        return f"{prefix}empty" if prefix else "empty"
    h = hashlib.sha256(s.encode()).hexdigest()[:8]
    return f"{prefix}{h}" if prefix else h
