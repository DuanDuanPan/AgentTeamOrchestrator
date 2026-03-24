"""共享 pytest fixture。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from ato.models.db import init_db


@pytest.fixture()  # type: ignore[untyped-decorator,unused-ignore]
def db_path(tmp_path: Path) -> Iterator[Path]:
    """返回临时数据库路径（不自动初始化）。"""
    yield tmp_path / ".ato" / "state.db"


@pytest.fixture()  # type: ignore[untyped-decorator,unused-ignore]
async def initialized_db_path(db_path: Path) -> AsyncIterator[Path]:
    """返回已初始化的数据库路径。"""
    await init_db(db_path)
    yield db_path
