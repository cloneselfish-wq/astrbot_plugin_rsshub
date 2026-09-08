"""B站视频推送冷却仓库（bilibili_rss 分支定制）

基于 rsshub_bilibili_video_seen 表记录 BV 号最近一次成功推送的
时间戳，支撑同视频推送冷却（24h/48h/永久）。
"""

from __future__ import annotations

import time

from sqlmodel import select

from ..utils import get_logger
from .database import get_database
from .models import BilibiliVideoSeenORM

logger = get_logger()


class BilibiliVideoStore:
    """BV 号推送冷却存储。"""

    async def get_last_pushed_at(self, bv_id: str) -> float | None:
        """返回 BV 号最近一次成功推送的 epoch 秒；从未推送返回 None。"""
        db = get_database()
        async with db.get_session() as session:
            row = (
                await session.execute(
                    select(BilibiliVideoSeenORM).where(
                        BilibiliVideoSeenORM.bv_id == str(bv_id)
                    )
                )
            ).first()
            if row is None:
                return None
            return float(row[0].last_pushed_at or 0.0)

    async def record_pushed(
        self, bv_id: str, *, feed_id: int = 0, title: str = ""
    ) -> None:
        """记录/刷新 BV 号最近推送时间（仅在成功追加推送后调用）。"""
        db = get_database()
        now = time.time()
        async with db.get_session() as session:
            row = (
                await session.execute(
                    select(BilibiliVideoSeenORM).where(
                        BilibiliVideoSeenORM.bv_id == str(bv_id)
                    )
                )
            ).first()
            orm_row = row[0] if row is not None else None
            if orm_row is None:
                session.add(
                    BilibiliVideoSeenORM(
                        bv_id=str(bv_id),
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
        logger.debug("[bilibili] 冷却记录已更新: bv=%s", bv_id)
