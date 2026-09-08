"""ORM 模型定义模块

定义数据库表结构对应的 SQLModel 模型。
所有模型继承自 RSSHubBaseModel，共享同一个 metadata。

注意:
    此模块仅包含 ORM 模型定义和数据结构，不包含业务逻辑方法。
    业务逻辑应放在领域层 (domain/entities/) 或仓库实现中。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field

from ...shared.constants import INHERIT_VALUE
from .database import RSSHubBaseModel

EFFECTIVE_OPTION_KEYS = (
    "send_mode",
    "length_limit",
    "display_author",
    "display_via",
    "display_title",
    "display_entry_tags",
    "style",
    "display_media",
)


# ============================================================================
# ORM 模型
# ============================================================================


class UserORM(RSSHubBaseModel, table=True):
    """用户 ORM 模型，映射 rsshub_user 表。"""

    __tablename__ = "rsshub_user"

    id: str = Field(default=None, primary_key=True, description="用户ID")
    state: int = Field(default=1, description="用户状态: -1=已封禁, 1=用户")

    interval: int = Field(default=INHERIT_VALUE, description="监控间隔(分钟)")
    notify: int = Field(default=INHERIT_VALUE, description="是否通知: 0=禁用, 1=启用")
    send_mode: int = Field(
        default=INHERIT_VALUE,
        description="发送模式: -1=仅链接, 0=自动, 1=直接发送",
    )
    handlers: str = Field(default="[]", description="内容处理 handlers JSON")
    length_limit: int = Field(default=INHERIT_VALUE, description="长度限制")
    display_author: int = Field(
        default=INHERIT_VALUE, description="显示作者: -1=禁用, 0=自动, 1=强制"
    )
    display_via: int = Field(
        default=INHERIT_VALUE,
        description="显示来源: -2=完全禁用, -1=仅链接, 0=自动, 1=强制",
    )
    display_title: int = Field(
        default=INHERIT_VALUE, description="显示标题: -1=禁用, 0=自动, 1=强制"
    )
    display_entry_tags: int = Field(default=INHERIT_VALUE, description="显示标签")
    style: int = Field(
        default=INHERIT_VALUE,
        description="推送排版策略: 0=自动, 1=RSSRT, 2=原始顺序",
    )
    display_media: int = Field(
        default=INHERIT_VALUE, description="显示媒体: -1=禁用, 0=启用"
    )
    default_target_session: str | None = Field(
        default=None,
        max_length=255,
        description="默认推送目标会话(unified_msg_origin)",
    )
    needs_binding_notice: int = Field(default=0, description="是否需要提示绑定推送目标")

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="创建时间",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column_kwargs={"onupdate": lambda: datetime.now(timezone.utc)},
        description="更新时间",
    )


class FeedORM(RSSHubBaseModel, table=True):
    """Feed ORM 模型，映射 rsshub_feed 表。"""

    __tablename__ = "rsshub_feed"

    id: int | None = Field(default=None, primary_key=True)
    state: int = Field(default=1, description="Feed状态: 0=停用, 1=启用")
    link: str = Field(max_length=4096, unique=True, description="Feed链接")
    title: str = Field(max_length=1024, description="Feed标题")
    entry_hashes: list[Any] | None = Field(
        default=None, sa_column=Column(JSON), description="条目哈希历史"
    )
    etag: str | None = Field(default=None, max_length=128, description="ETag")
    last_modified: datetime | None = Field(default=None, description="最后修改时间")

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="创建时间",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column_kwargs={"onupdate": lambda: datetime.now(timezone.utc)},
        description="更新时间",
    )


class SubORM(RSSHubBaseModel, table=True):
    """订阅 ORM 模型，映射 rsshub_sub 表。"""

    __tablename__ = "rsshub_sub"

    id: int | None = Field(default=None, primary_key=True)
    state: int = Field(default=1, description="订阅状态: 0=停用, 1=启用")

    user_id: str = Field(foreign_key="rsshub_user.id", description="用户ID")
    feed_id: int = Field(foreign_key="rsshub_feed.id", description="FeedID")

    title: str = Field(default="", max_length=1024, description="订阅标题")
    tags: str = Field(default="", max_length=255, description="标签")
    target_session: str | None = Field(
        default=None,
        max_length=255,
        description="订阅推送目标会话",
    )
    platform_name: str | None = Field(
        default=None,
        max_length=64,
        description="平台类型名",
    )
    bot_self_id: str = Field(
        default="",
        max_length=64,
        description="订阅创建时所用 bot 的 self_id（合并转发节点身份）",
    )

    interval: int = Field(default=INHERIT_VALUE, description="监控间隔(分钟)")
    next_check_time: datetime | None = Field(default=None, description="下次检查时间")
    notify: int = Field(default=INHERIT_VALUE, description="是否通知")
    send_mode: int = Field(
        default=INHERIT_VALUE,
        description="发送模式: -100=继承, -1=仅链接, 0=自动, 1=直接发送",
    )
    length_limit: int = Field(default=INHERIT_VALUE, description="长度限制")
    display_author: int = Field(default=INHERIT_VALUE, description="显示作者")
    display_via: int = Field(default=INHERIT_VALUE, description="显示来源")
    display_title: int = Field(default=INHERIT_VALUE, description="显示标题")
    display_entry_tags: int = Field(default=INHERIT_VALUE, description="显示标签")
    style: int = Field(default=INHERIT_VALUE, description="推送排版策略")
    display_media: int = Field(default=INHERIT_VALUE, description="显示媒体")
    handlers_mode: str = Field(
        default="inherit",
        max_length=16,
        description="handlers 继承模式",
    )
    handlers: str = Field(default="[]", description="内容处理 handlers JSON")

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="创建时间",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column_kwargs={"onupdate": lambda: datetime.now(timezone.utc)},
        description="更新时间",
    )


class PushHistoryORM(RSSHubBaseModel, table=True):
    """推送历史 ORM 模型，映射 rsshub_push_history 表。"""

    __tablename__ = "rsshub_push_history"

    id: int | None = Field(default=None, primary_key=True)
    sub_id: int | None = Field(
        default=None, foreign_key="rsshub_sub.id", description="订阅ID"
    )
    user_id: str = Field(foreign_key="rsshub_user.id", description="用户ID")
    feed_id: int | None = Field(
        default=None, foreign_key="rsshub_feed.id", description="FeedID"
    )
    source_type: str = Field(default="feed", max_length=16, description="来源类型")
    source_key: str | None = Field(
        default=None, max_length=255, description="来源跟踪键"
    )

    content: str = Field(default="", description="格式化后的消息内容")
    raw_xml: str | None = Field(default=None, description="XML 推送原始内容")
    media_urls: list[str] | None = Field(
        default=None, sa_column=Column(JSON), description="媒体URL列表"
    )
    handler_trace: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSON), description="handler 执行摘要"
    )

    entry_title: str = Field(default="", max_length=1024, description="条目标题")
    entry_link: str = Field(default="", max_length=4096, description="条目链接")
    entry_guid: str | None = Field(default=None, max_length=512, description="条目GUID")

    feed_title: str = Field(default="", max_length=1024, description="Feed标题")
    feed_link: str = Field(default="", max_length=4096, description="Feed链接")

    platform_name: str | None = Field(
        default=None, max_length=64, description="平台名称"
    )
    target_session: str | None = Field(
        default=None, max_length=255, description="目标会话"
    )

    status: str | None = Field(
        default=None,
        max_length=16,
        description="状态: pending/success/failed/stopped/skipped",
    )
    retry_count: int = Field(default=0, description="重试次数")
    max_retries: int = Field(default=3, description="最大重试次数")
    fail_reason: str | None = Field(
        default=None, max_length=512, description="失败原因"
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="创建时间",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column_kwargs={"onupdate": lambda: datetime.now(timezone.utc)},
        description="更新时间",
    )
    completed_at: datetime | None = Field(default=None, description="完成时间")


class MigrationRecordORM(RSSHubBaseModel, table=True):
    """迁移记录 ORM 模型，映射 rsshub_migration_record 表。"""

    __tablename__ = "rsshub_migration_record"

    version: str = Field(primary_key=True, max_length=32, description="迁移版本号")
    applied_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="应用时间",
    )
    description: str = Field(default="", max_length=256, description="迁移描述")


class BilibiliVideoSeenORM(RSSHubBaseModel, table=True):
    """B站视频推送冷却记录（bilibili_rss 分支定制）。

    以 BV 号为主键记录最近一次成功推送的时间戳，用于实现
    同一视频的推送冷却（24h/48h/永久，由 bilibili.dedup_hours 配置）。
    """

    __tablename__ = "rsshub_bilibili_video_seen"

    bv_id: str = Field(primary_key=True, max_length=32, description="B站视频 BV 号")
    feed_id: int = Field(default=0, description="首次推送来源 Feed ID")
    title: str = Field(default="", max_length=512, description="视频标题")
    last_pushed_at: float = Field(default=0.0, description="最近推送时间戳（epoch 秒）")
