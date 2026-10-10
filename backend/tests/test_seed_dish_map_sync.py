"""承接表改动的同步契约（seed.py 里的 _seed_dish_map）。

原先的实现按「行数够了就跳过」判断幂等：本机数据库已有 20 行时，往
seed_dish_map.json 补第 21 行会被当成「已经灌过了」而静默跳过，只有删库或 force 才生效。
而补覆盖率改的恰恰就是这个文件——所以这条路径必须有测试守住，否则「加了映射但接口行为不变」。

运行：python -m pytest backend/tests/test_seed_dish_map_sync.py -v
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from app.core.database import create_all, get_engine, init_engine, session_scope  # noqa: E402
from app.models import DishRecipeMap  # noqa: E402
from app.services.seed import seed_database  # noqa: E402

REAL_DATA_DIR = BACKEND_DIR / "app" / "data"
SEED_FILES = ("seed_ingredients.json", "seed_recipes.json", "seed_dish_map.json")


@pytest.fixture()
def seeded(tmp_path):
    """一套可随意改写的种子文件 + 独立数据库。"""

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for name in SEED_FILES:
        shutil.copy(REAL_DATA_DIR / name, data_dir / name)

    settings = Settings(
        database_url="sqlite+pysqlite:///%s" % (tmp_path / "sync.db").as_posix(),
        data_dir=data_dir,
        models_dir=tmp_path / "models-not-delivered",
    )
    init_engine(settings)
    create_all(get_engine())
    return settings


def _map_path(settings) -> Path:
    return settings.data_dir / "seed_dish_map.json"


def _read_map(settings) -> dict:
    return json.loads(_map_path(settings).read_text(encoding="utf-8"))


def _write_map(settings, payload: dict) -> None:
    _map_path(settings).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _rows(settings) -> dict:
    with session_scope() as session:
        return {
            row.class_index: (row.recipe_id, row.alias)
            for row in session.scalars(select(DishRecipeMap))
        }


# --------------------------- 幂等 ---------------------------

def test_first_seed_writes_every_row(seeded):
    with session_scope() as session:
        summary = seed_database(session, seeded)
    assert summary["inserted"] is True
    assert summary["dish_map"] == len(_read_map(seeded))
    assert len(_rows(seeded)) == len(_read_map(seeded))


def test_second_seed_reports_no_change_and_keeps_row_count(seeded):
    with session_scope() as session:
        seed_database(session, seeded)
    with session_scope() as session:
        again = seed_database(session, seeded)
    assert again["inserted"] is False, "没有变化时不该报告写入"
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(DishRecipeMap)) == len(
            _read_map(seeded)
        )


# --------------------------- 改动同步（回归点） ---------------------------

def test_added_row_is_synced_without_force(seeded):
    """补一行映射，重灌种子就该生效——旧实现会在这里静默跳过。"""

    with session_scope() as session:
        seed_database(session, seeded)
    before = _rows(seeded)
    assert 3 not in before, "这条用例要挑一个种子文件里没有的类别"

    payload = _read_map(seeded)
    payload["3"] = {"recipe_id": 124, "alias": "豆腐花"}
    _write_map(seeded, payload)

    with session_scope() as session:
        summary = seed_database(session, seeded)
    assert summary["inserted"] is True, "新增映射应报告为一次写入"
    assert _rows(seeded)[3] == (124, "豆腐花")


def test_changed_row_is_synced_without_force(seeded):
    """改指向也要生效：把某类改接到别的菜谱，重灌后必须是新值。"""

    with session_scope() as session:
        seed_database(session, seeded)
    payload = _read_map(seeded)
    index = sorted(payload, key=int)[0]
    original = payload[index]["recipe_id"]
    payload[index]["recipe_id"] = 101
    payload[index]["alias"] = "番茄炒蛋"
    _write_map(seeded, payload)

    with session_scope() as session:
        seed_database(session, seeded)
    assert _rows(seeded)[int(index)] == (101, "番茄炒蛋")
    assert original != 101


def test_alias_only_change_is_synced(seeded):
    """只补别名（recipe_id 不变）也得同步，别名是手输检索的键。"""

    with session_scope() as session:
        seed_database(session, seeded)
    payload = _read_map(seeded)
    index = sorted(payload, key=int)[0]
    payload[index]["alias"] = "测试别名"
    _write_map(seeded, payload)

    with session_scope() as session:
        seed_database(session, seeded)
    assert _rows(seeded)[int(index)][1] == "测试别名"


def test_rows_outside_the_seed_file_are_left_alone(seeded):
    """种子里没有的行不动——可能是本机手工加的，不该被悄悄删掉。"""

    with session_scope() as session:
        seed_database(session, seeded)
    with session_scope() as session:
        session.add(DishRecipeMap(class_index=200, recipe_id=101, alias=None))
    with session_scope() as session:
        seed_database(session, seeded)
    assert _rows(seeded)[200] == (101, None)


def test_force_rebuilds_the_table_from_the_file(seeded):
    """force 是整表重建：种子里没有的行会被清掉。"""

    with session_scope() as session:
        seed_database(session, seeded)
    with session_scope() as session:
        session.add(DishRecipeMap(class_index=200, recipe_id=101, alias=None))
    with session_scope() as session:
        seed_database(session, seeded, force=True)
    rows = _rows(seeded)
    assert 200 not in rows
    assert len(rows) == len(_read_map(seeded))
