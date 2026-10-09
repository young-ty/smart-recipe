"""schema 迁移测试（设计方案 6.2）。

- v1 → v2：可再生的三张表（识别日志 / 生成缓存 / 类别承接表）直接重建，
  菜谱与食材主数据原样保留。
- v2 → v3：`recipes` 主表要动结构（rating 放开 NOT NULL、新增 origin），只能重建表；
  重建期间必须关掉外键校验，否则 ON DELETE CASCADE 会把 recipe_ingredients 与
  recipe_steps 一起清空——菜谱会变成空壳，这里专门有用例守这条。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.core.database import SCHEMA_VERSION, create_all, migrate_schema
from app.models.base import Base

# v1 的 recognition_logs 结构，取自旧方向发布版本（列名与 v2 不兼容）
_V1_LOG_TABLE = """
CREATE TABLE recognition_logs (
    id INTEGER NOT NULL,
    detection_id VARCHAR(36) NOT NULL,
    model_version VARCHAR(64) NOT NULL,
    elapsed_ms INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    image_sha256 VARCHAR(64),
    degraded BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (detection_id)
)
"""


# v2 的 recipes 结构：rating 还是 NOT NULL，且没有 origin 列
_V2_RECIPES_TABLE = """
CREATE TABLE recipes (
    id INTEGER NOT NULL,
    name VARCHAR(64) NOT NULL,
    cook_time_minutes INTEGER NOT NULL,
    difficulty VARCHAR(8) NOT NULL,
    rating FLOAT NOT NULL,
    servings INTEGER NOT NULL,
    description TEXT,
    image_url VARCHAR(255),
    PRIMARY KEY (id),
    UNIQUE (name)
)
"""


def _url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.as_posix()}"


def _make_v1_database(path: Path) -> None:
    """造一个旧方向的库：菜谱主数据在，识别日志还是 v1 结构，版本号停在 1。"""

    engine = create_engine(_url(path), future=True)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE recognition_logs"))
        connection.execute(text(_V1_LOG_TABLE))
        connection.execute(
            text(
                "INSERT INTO recognition_logs "
                "(id, detection_id, model_version, elapsed_ms, result_json, degraded, created_at) "
                "VALUES (1, 'legacy-detection', 'mock-foodinsseg', 12, '{}', 1, '2026-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO recipes (id, name, cook_time_minutes, difficulty, rating, servings) "
                "VALUES (7, 'legacy', 20, '简单', 4.5, 2)"
            )
        )
        connection.execute(text("PRAGMA user_version = 1"))
    engine.dispose()


def _make_v2_database(path: Path) -> None:
    """造一个成品菜方向的库：rating 仍不允许为空，菜谱下挂着食材与步骤，版本号停在 2。"""

    engine = create_engine(_url(path), future=True)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE recipes"))
        connection.execute(text(_V2_RECIPES_TABLE))
        connection.execute(
            text(
                "INSERT INTO recipes (id, name, cook_time_minutes, difficulty, rating, servings) "
                "VALUES (7, 'legacy', 20, '简单', 4.5, 2)"
            )
        )
        connection.execute(
            text("INSERT INTO ingredients (id, name, category) VALUES (1, '五花肉', '肉类')")
        )
        connection.execute(
            text(
                "INSERT INTO recipe_ingredients (recipe_id, ingredient_id, is_required, amount) "
                "VALUES (7, 1, 1, '600 克')"
            )
        )
        connection.execute(
            text("INSERT INTO recipe_steps (recipe_id, step_no, content) VALUES (7, 1, '切块焯水')")
        )
        connection.execute(text("PRAGMA user_version = 2"))
    engine.dispose()


def _user_version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()


def _columns(path: Path, table: str) -> set:
    connection = sqlite3.connect(path)
    try:
        return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
    finally:
        connection.close()


def _notnull(path: Path, table: str) -> dict:
    connection = sqlite3.connect(path)
    try:
        return {
            row[1]: bool(row[3]) for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
    finally:
        connection.close()


def _table_shape(path: Path, table: str) -> set:
    """列名 / 类型 / NOT NULL / 默认值 / 主键序号的快照，用来比对两条建表路径。"""

    connection = sqlite3.connect(path)
    try:
        return {
            (row[1], row[2].upper(), bool(row[3]), (row[4] or "").strip(), row[5])
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
    finally:
        connection.close()


def _index_names(path: Path, table: str) -> set:
    connection = sqlite3.connect(path)
    try:
        return {row[1] for row in connection.execute(f'PRAGMA index_list("{table}")')}
    finally:
        connection.close()


def _scalar(path: Path, sql: str):
    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql).fetchone()[0]
    finally:
        connection.close()


@pytest.fixture()
def v1_database(tmp_path: Path):
    db_path = tmp_path / "v1.db"
    _make_v1_database(db_path)
    engine = create_engine(_url(db_path), future=True)
    try:
        yield engine, db_path
    finally:
        engine.dispose()


@pytest.fixture()
def v2_database(tmp_path: Path):
    db_path = tmp_path / "v2.db"
    _make_v2_database(db_path)
    engine = create_engine(_url(db_path), future=True)
    try:
        yield engine, db_path
    finally:
        engine.dispose()


def test_sample_database_really_is_v1(v1_database):
    _engine, db_path = v1_database
    assert _user_version(db_path) == 1
    assert "detection_id" in _columns(db_path, "recognition_logs")


def test_migrate_schema_bumps_version_exactly_once(v1_database):
    engine, db_path = v1_database
    assert migrate_schema(engine) is True
    assert _user_version(db_path) == SCHEMA_VERSION
    assert migrate_schema(engine) is False, "已经是当前版本就不该再动库"


def test_create_all_upgrades_v1_log_table_to_v2_shape(v1_database):
    engine, db_path = v1_database
    create_all(engine)
    columns = _columns(db_path, "recognition_logs")
    assert "recognition_id" in columns
    assert "detection_id" not in columns, "旧列名会误导排查，必须换成成品菜口径"
    assert {"class_count", "top1_index", "top1_confidence"} <= columns


def test_migration_rebuilds_recyclable_tables(v1_database):
    engine, db_path = v1_database
    create_all(engine)
    connection = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()
    assert {"recognition_logs", "generated_recipes", "dish_recipe_map"} <= tables


def test_migration_drops_legacy_log_rows_without_touching_recipe_data(v1_database):
    """日志是流水、生成缓存可重建，重建无损；菜谱主数据必须原样保留。"""

    engine, db_path = v1_database
    create_all(engine)
    assert _scalar(db_path, "SELECT COUNT(*) FROM recognition_logs") == 0
    assert _scalar(db_path, "SELECT COUNT(*) FROM recipes WHERE id = 7") == 1
    assert _scalar(db_path, "SELECT difficulty FROM recipes WHERE id = 7") == "简单"


def test_migrated_database_accepts_v2_writes(v1_database):
    engine, db_path = v1_database
    create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO recognition_logs "
                "(recognition_id, model_version, class_count, top1_index, top1_confidence, "
                " elapsed_ms, result_json, degraded, created_at) "
                "VALUES ('abc', 'dish-resnet50d-208-onnx', 208, 77, 0.819, 65, '{}', 0, "
                "        '2026-09-20 00:00:00')"
            )
        )
    assert _scalar(db_path, "SELECT top1_index FROM recognition_logs") == 77


def test_current_database_keeps_its_rows_when_create_all_runs_again(tmp_path: Path):
    """已经是 v2 的库再次启动时只能补建缺失表，不能清掉已有数据。"""

    db_path = tmp_path / "v2.db"
    engine = create_engine(_url(db_path), future=True)
    create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO dish_recipe_map (class_index, recipe_id, alias) VALUES (77, NULL, NULL)")
        )
    create_all(engine)
    assert _user_version(db_path) == SCHEMA_VERSION
    assert _scalar(db_path, "SELECT COUNT(*) FROM dish_recipe_map") == 1
    engine.dispose()


def test_migrate_schema_leaves_non_sqlite_backends_alone():
    """非 SQLite 后端由运维先行升级，代码不擅自重建表。"""

    class _StubEngine:
        class dialect:  # noqa: N801 - 只需 name 属性
            name = "postgresql"

    assert migrate_schema(_StubEngine()) is False


# ------------------------------------------------- v2 → v3（生成结果可回写菜谱库）

def test_sample_database_really_is_v2(v2_database):
    _engine, db_path = v2_database
    assert _user_version(db_path) == 2
    assert "origin" not in _columns(db_path, "recipes")
    assert _notnull(db_path, "recipes")["rating"] is True


def test_migration_from_v2_opens_rating_and_adds_origin(v2_database):
    engine, db_path = v2_database
    create_all(engine)
    assert _user_version(db_path) == SCHEMA_VERSION
    columns = _notnull(db_path, "recipes")
    assert columns["rating"] is False, "生成后入库的菜谱没有评分，rating 必须可空"
    assert columns["origin"] is True, "来源标记是必填字段"


def test_migration_from_v2_backfills_origin_for_existing_rows(v2_database):
    """老数据没有来源标记，一律按人工整理的种子数据处理。"""

    engine, db_path = v2_database
    create_all(engine)
    assert _scalar(db_path, "SELECT origin FROM recipes WHERE id = 7") == "seed"


def test_migration_keeps_recipe_ingredients_and_steps(v2_database):
    """重建主表时若忘了关外键，CASCADE 会把这两张表清空——这条用例专治这个坑。"""

    engine, db_path = v2_database
    create_all(engine)
    assert _scalar(db_path, "SELECT COUNT(*) FROM recipe_ingredients") == 1
    assert _scalar(db_path, "SELECT COUNT(*) FROM recipe_steps") == 1
    assert _scalar(db_path, "SELECT amount FROM recipe_ingredients WHERE recipe_id = 7") == "600 克"
    assert _scalar(db_path, "SELECT content FROM recipe_steps WHERE recipe_id = 7") == "切块焯水"


def test_migration_from_v2_recreates_the_composite_index(v2_database):
    engine, db_path = v2_database
    create_all(engine)
    assert "ix_recipes_difficulty_cook_time" in _index_names(db_path, "recipes")


def test_migrated_recipes_table_matches_a_freshly_created_one(v2_database, tmp_path: Path):
    """迁移出来的表必须与 ORM 新建的一模一样，否则两条升级路径会悄悄分叉。"""

    engine, db_path = v2_database
    create_all(engine)
    fresh_path = tmp_path / "fresh.db"
    fresh_engine = create_engine(_url(fresh_path), future=True)
    try:
        create_all(fresh_engine)
    finally:
        fresh_engine.dispose()
    assert _table_shape(db_path, "recipes") == _table_shape(fresh_path, "recipes")


def test_migrated_database_accepts_generated_rows(v2_database):
    engine, db_path = v2_database
    create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO recipes "
                "(id, name, cook_time_minutes, difficulty, rating, servings, origin) "
                "VALUES (10000, '大模型菜', 15, '简单', NULL, 2, 'generated')"
            )
        )
    assert _scalar(db_path, "SELECT COUNT(*) FROM recipes WHERE rating IS NULL") == 1
    assert _scalar(db_path, "SELECT origin FROM recipes WHERE id = 10000") == "generated"


def test_migrate_schema_from_v2_runs_only_once(v2_database):
    engine, db_path = v2_database
    assert migrate_schema(engine) is True
    assert _user_version(db_path) == SCHEMA_VERSION
    assert migrate_schema(engine) is False, "已经是当前版本就不该再动库"
