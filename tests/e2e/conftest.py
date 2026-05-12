"""pytest-asyncio strict mode 下手动声明 marker；这里集中开启 auto 模式
让 @pytest.mark.asyncio 不必每次写。"""
import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "asyncio" in item.keywords:
            continue
        item.add_marker(pytest.mark.asyncio)
