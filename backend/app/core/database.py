"""数据库引擎、会话与建表辅助（含极简 schema 迁移）。"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..models import Base
from .config import Settings, get_settings

logger = logging.getLogger(__name__)

# v2.0 起 schema 版本写入 SQLite 的 PRAGMA user_version。
# 1 = 旧方向（食材识别 / 匹配度推荐），2 = 成品菜方向，3 = 生成结果可回写菜谱库。
# v2 只改了日志表结构并新增承接表与生成缓存，这三张表都是可再生的：
# 日志是流水，承接表与缓存由种子数据与生成结果重建，所以直接重建而不写逐列迁移。
# v3 要动 recipes 主表（rating 放开 NOT NULL、新增 origin），主数据不能重建，只能改表结构。
# v4 新增 users / user_sessions / user_memories 三张表：纯新增，不碰已有表，
# 建表交给 Base.metadata.create_all，这里只把版本号一起抬上去。
# v5 给 users 加账号级模型 Key 的四列：SQLite 能直接 ADD COLUMN，主数据不用重建。
SCHEMA_VERSION = 5
_DISPOSABLE_TABLES = ("recognition_logs", "generated_recipes", "dish_recipe_map")

# v5：账号级模型 Key（core/crypto.py 加密后落库，明文不进库）
_USERS_ADDED_COLUMNS = {
    "llm_api_key_enc": "TEXT",
    "llm_base_url": "VARCHAR(200)",
    "llm_model": "VARCHAR(64)",
    "llm_key_updated_at": "DATETIME",
}

_RECIPES_COLUMNS = (
    "id",
    "name",
    "cook_time_minutes",
    "difficulty",
    "rating",
    "servings",
    "description",
    "image_url",
)

# v3 的 recipes 结构：rating 可为空（生成后入库的菜谱没有评分），新增 origin
_RECIPES_V3_DDL = """
CREATE TABLE recipes_v3 (
    id INTEGER NOT NULL,
    name VARCHAR(64) NOT NULL,
    cook_time_minutes INTEGER NOT NULL,
    difficulty VARCHAR(8) NOT NULL,
    rating FLOAT,
    servings INTEGER NOT NULL,
    description TEXT,
    image_url VARCHAR(255),
    origin VARCHAR(16) NOT NULL DEFAULT 'seed',
    PRIMARY KEY (id),
    UNIQUE (name)
)
"""

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None


def _configure_sqlite(engine: Engine) -> None:
    """SQLite 默认不校验外键，显式开启以保证级联删除生效。"""

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # pragma: no cover - 驱动回调
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_db_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args, future=True)
    if database_url.startswith("sqlite"):
        _configure_sqlite(engine)
    return engine


def init_engine(settings: Optional[Settings] = None) -> Engine:
    """初始化全局引擎与会话工厂；重复调用会重建（测试用内存库时依赖此行为）。"""

    global _engine, _session_factory
    settings = settings or get_settings()
    if _engine is not None:
        _engine.dispose()
    _engine = create_db_engine(settings.database_url)
    _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def get_session_factory() -> sessionmaker:
    if _session_factory is None:
        init_engine()
    assert _session_factory is not None
    return _session_factory


def _table_columns(cursor, table: str) -> dict:
    """{列名: 是否 NOT NULL}；表不存在时返回空字典。"""

    rows = cursor.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {row[1]: bool(row[3]) for row in rows}


def _add_missing_columns(engine: Engine, table: str, columns: dict) -> bool:
    """给已有表补列，返回是否动过表。SQLite 的 ADD COLUMN 不用重建表，主数据原样留着。

    这里走 DBAPI 原生游标：`Base.metadata.create_all` 只建缺失的表，表已经在了就
    不会补列，老库升级得靠这一步。
    """

    raw = engine.raw_connection()
    cursor = raw.cursor()
    changed = False
    try:
        present = _table_columns(cursor, table)
        if not present:
            return False
        for name, ddl in columns.items():
            if name in present:
                continue
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {ddl}')
            changed = True
        raw.commit()
    finally:
        cursor.close()
        raw.close()
    return changed


def _upgrade_recipes_table(engine: Engine) -> bool:
    """把 recipes 升到 v3（rating 放开 NOT NULL、新增 origin），返回是否重建过。

    SQLite 改不了列的 NOT NULL，只能重建表。重建期间必须**关掉外键校验**：
    否则 DROP TABLE recipes 会按 ON DELETE CASCADE 把 recipe_ingredients /
    recipe_steps 一并删掉，生成后入库的菜谱就只剩空壳。SQLite 的 PRAGMA 在事务里
    不生效，所以这里走 DBAPI 原生游标、自己控事务边界，最后用 foreign_key_check 兜底。
    """

    raw = engine.raw_connection()
    cursor = raw.cursor()
    try:
        columns = _table_columns(cursor, "recipes")
        if not columns:
            return False
        if "origin" in columns and not columns.get("rating"):
            return False

        present = [name for name in _RECIPES_COLUMNS if name in columns]
        origin = "origin" if "origin" in columns else "'seed'"
        columns_sql = ", ".join(present + ["origin"])
        values_sql = ", ".join(present + [origin])
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("BEGIN")
        try:
            cursor.execute("DROP TABLE IF EXISTS recipes_v3")
            cursor.execute(_RECIPES_V3_DDL)
            cursor.execute(
                f"INSERT INTO recipes_v3 ({columns_sql}) SELECT {values_sql} FROM recipes"
            )
            cursor.execute("DROP TABLE recipes")
            cursor.execute("ALTER TABLE recipes_v3 RENAME TO recipes")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS ix_recipes_difficulty_cook_time"
                " ON recipes (difficulty, cook_time_minutes)"
            )
            violations = cursor.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                logger.warning("迁移后外键校验有 %d 条异常：%s", len(violations), violations[:3])
            cursor.execute("COMMIT")
        except Exception:
            cursor.execute("ROLLBACK")
            raise
        finally:
            cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()
        raw.close()
    return True


def migrate_schema(engine: Optional[Engine] = None) -> bool:
    """把旧版本的数据库就地升级到 SCHEMA_VERSION，返回是否发生了重建。

    只对 SQLite 生效；其他后端假定由运维先行升级。
    """

    engine = engine or get_engine()
    if engine.dialect.name != "sqlite":
        return False

    with engine.begin() as connection:
        current = connection.execute(text("PRAGMA user_version")).scalar() or 0
    if current >= SCHEMA_VERSION:
        return False

    if current < 2:
        with engine.begin() as connection:
            for table in _DISPOSABLE_TABLES:
                connection.execute(text(f'DROP TABLE IF EXISTS "{table}"'))

    _upgrade_recipes_table(engine)

    _add_missing_columns(engine, "users", _USERS_ADDED_COLUMNS)

    with engine.begin() as connection:
        connection.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))
    return True


def create_all(engine: Optional[Engine] = None) -> None:
    target = engine or get_engine()
    migrate_schema(target)
    Base.metadata.create_all(bind=target)
    if target.dialect.name == "sqlite":
        with target.begin() as connection:
            connection.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))


def drop_all(engine: Optional[Engine] = None) -> None:
    Base.metadata.drop_all(bind=engine or get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：每个请求一个会话。"""

    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
