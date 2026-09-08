"""V3 迁移：B站视频冷却表改为 (BV号, 目标会话) 组合主键

历史背景：
- 旧表 rsshub_bilibili_video_seen 仅以 bv_id 为主键，冷却全局共享
- 新需求：冷却改为推送维度且按群独立，同一视频在 A 群冷却
  不影响 B 群首次推送
- ORM 模型已增加 target_session 组合主键列

修复方案：
- 新安装：create_all 已按新结构建表（含 target_session），直接跳过
- 旧库：检测到表存在但缺少 target_session 列时，删除并按新结构重建
  （该表仅是冷却缓存，数据丢失可接受，最多导致一次重复推送）
"""

from __future__ import annotations

from ...utils import get_logger

logger = get_logger()

_CREATE_TABLE_SQL = """
CREATE TABLE rsshub_bilibili_video_seen (
    bv_id VARCHAR(32) NOT NULL,
    target_session VARCHAR(128) NOT NULL DEFAULT '',
    feed_id INTEGER NOT NULL DEFAULT 0,
    title VARCHAR(512) NOT NULL DEFAULT '',
    last_pushed_at FLOAT NOT NULL DEFAULT 0.0,
    PRIMARY KEY (bv_id, target_session)
)
"""


async def upgrade(conn) -> None:
    """执行 V3 迁移：冷却表重建为 (bv_id, target_session) 组合主键"""

    async def _table_exists(table: str) -> bool:
        result = await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        return result.fetchone() is not None

    async def _column_exists(table: str, column: str) -> bool:
        result = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
        return any(str(row[1]) == column for row in result.fetchall())

    if not await _table_exists("rsshub_bilibili_video_seen"):
        logger.info("迁移 V3: rsshub_bilibili_video_seen 表不存在，跳过")
        return

    if await _column_exists("rsshub_bilibili_video_seen", "target_session"):
        logger.info("迁移 V3: target_session 列已存在，无需重建")
        return

    try:
        await conn.exec_driver_sql("DROP TABLE rsshub_bilibili_video_seen")
        await conn.exec_driver_sql(_CREATE_TABLE_SQL)
        logger.info("迁移 V3: 已重建 rsshub_bilibili_video_seen（组合主键）")
    except Exception as e:
        logger.error("迁移 V3: 重建冷却表失败: %s", e)
        raise

    logger.info("迁移 V3 完成: B站视频冷却改为按群独立")
