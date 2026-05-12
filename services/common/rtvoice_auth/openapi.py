"""SP10 G4 — FastAPI OpenAPI schema 后处理：注入 securitySchemes Bearer 声明。

SP8 D2 揭出：4 service `/openapi.json` 都缺 `components.securitySchemes`，
codegen 出的 TS client 没 OpenAPI.TOKEN 全局字段，调用方必须手写 Bearer 头。

修：每个 service main.py 调一次 `add_bearer_security_scheme(app)` 完成。
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


SECURITY_SCHEME_NAME = "rtvoice_auth"


def add_bearer_security_scheme(app: FastAPI) -> None:
    """覆盖 app.openapi() 让 schema 在 components 下含 Bearer 声明 + 给所有路由加 security require。

    调用示例（service main.py 末尾）::

        from rtvoice_auth.openapi import add_bearer_security_scheme
        add_bearer_security_scheme(app)
    """
    original_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = original_openapi() if callable(original_openapi) else get_openapi(
            title=app.title,
            version=app.version,
            description=app.description or "",
            routes=app.routes,
        )
        comps = schema.setdefault("components", {})
        ss = comps.setdefault("securitySchemes", {})
        ss[SECURITY_SCHEME_NAME] = {
            "type": "http",
            "scheme": "bearer",
            "description": "RTVoice API key (Bearer)，通过 admin CLI 颁发；scope 按 service 区分",
        }
        # 全局 security require（路由可单独 override 为空数组放公开 health/info）
        schema.setdefault("security", [{SECURITY_SCHEME_NAME: []}])
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
