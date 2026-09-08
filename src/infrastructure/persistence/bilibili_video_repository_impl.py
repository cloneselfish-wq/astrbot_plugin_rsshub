"""B站视频推送冷却仓库（bilibili_rss 分支定制）

基于 rsshub_bilibili_video_seen 表以 (BV号, 目标会话) 记录最近一次成功
推送的时间戳，支撑同视频按群独立的推送冷却（24h/48h/永久）。
"""

from __future__ import annotations

import time

from sqlmodel import select

from ..utils import get_logger
from .database import get_database
from .models import BilibiliVideoSeenORM

logger = get_logger()


class BilibiliVideoStore:
    """(BV号, 目标会话) 推送冷却存储，冷却按群独立。"""

    async def get_last_pushed_at(self, bv_id: str, target_session: str = "") -> float | None:
        """返回 (BV号, 会话) 最近一次成功推送的 epoch 秒；从未推送返回 None。"""
        db = get_database()
        async with db.get_session() as session:
            row = (
                await session.execute(
                    select(BilibiliVideoSeenORM).where(
                        BilibiliVideoSeenORM.bv_id == str(bv_id),
                        BilibiliVideoSeenORM.target_session == str(target_session or ""),
                    )
                )
            ).first()
            if row is None:
                return None
            return float(row[0].last_pushed_at or 0.0)

    async def record_pushed(
        self,
        bv_id: str,
        *,
        feed_id: int = 0,
        title: str = "",
        target_session: str = "",
    ) -> None:
        """记录/刷新 (BV号, 会话) 最近推送时间（仅在成功追加推送后调用）。"""
        db = get_database()
        now = time.time()
        session_key = str(target_session or "")
        async with db.get_session() as session:
            row = (
                await session.execute(
                    select(BilibiliVideoSeenORM).where(
                        BilibiliVideoSeenORM.bv_id == str(bv_id),
                        BilibiliVideoSeenORM.target_session == session_key,
                    )
                )
            ).first()
            orm_row = row[0] if row is not None else None
            if orm_row is None:
                session.add(
                    BilibiliVideoSeenORM(
                        bv_id=str(bv_id),
                        target_session=session_key,
                        feed_id=int(feed_id or 0),
                        title=str(title or "")[:512],
                        last_pushed_at=now,
                    )
                )
            else:
                orm_row.last_pushed_at = now
                orm_row.feed_id = int(feed_id or 0)
                if title:
                    orm_row.title = str(title)[:512]
                session.add(orm_row)
            await session.commit()
        logger.debug(
            "[bilibili] 冷却记录已更新: bv=%s, session=%s", bv_id, session_key
        )
