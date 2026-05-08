import pytest
import pytest_asyncio


@pytest_asyncio.fixture(autouse=False)
def _no_op():
    pass
