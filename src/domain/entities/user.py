"""
用户领域实体

代表系统中的一个用户，包含用户状态、默认订阅选项和推送配置。
不包含任何 ORM 或持久化逻辑。
"""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...shared.constants import INHERIT_VALUE, USER_STATE_BANNED, USER_STATE_USER
from .handlers import dump_handlers


class User(BaseModel):
    """
    用户领域实体

    代表系统中的一个用户，包含用户状态、默认订阅选项和推送配置。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    """用户ID（主键）"""

    state: int = Field(
        default=USER_STATE_USER, description="用户状态: -1=已封禁, 1=用户"
    )

    interval: int = Field(default=INHERIT_VALUE, description="监控间隔（分钟）")
    notify: int = Field(default=INHERIT_VALUE, description="是否通知: 0=禁用, 1=启用")
    send_mode: int = Field(
        default=INHERIT_VALUE,
        description="发送模式: -1=仅链接, 0=自动, 1=直接发送",
    )
    handler_specs: Any = Field(
        default_factory=list,
        alias="handlers",
        description="内容处理 handlers",
    )
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
        default=None, max_length=255, description="默认推送目标会话(unified_msg_origin)"
    )
    needs_binding_notice: int = Field(default=0, description="是否需要提示绑定推送目标")

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="创建时间"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="更新时间"
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_handlers_field(cls, value: Any) -> Any:
        if isinstance(value, dict):
            payload = dict(value)
            raw_handlers = payload.get("handlers", payload.get("handler_specs"))
            payload["handler_specs"] = dump_handlers(raw_handlers)
            return payload
        return value

    def get_handlers(self) -> list[dict[str, Any]]:
        return dump_handlers(self.handler_specs)

    def set_handlers(self, value: Any) -> "User":
        self.handler_specs = dump_handlers(value)
        self.updated_at = datetime.now(timezone.utc)
        return self

    def clear_handlers(self) -> "User":
        self.handler_specs = []
        self.updated_at = datetime.now(timezone.utc)
        return self

    @property
    def handlers(self) -> list[dict[str, Any]]:
        """Backward-compatible read-only alias."""
        return self.get_handlers()

    def is_active(self) -> bool:
        """检查用户是否处于启用状态（非封禁）"""
        return self.state != USER_STATE_BANNED

    def is_admin(self) -> bool:
        """兼容旧调用；插件不再维护用户管理员状态。"""
        return False

    def activate(self) -> "User":
        """将用户状态设为启用"""
        self.state = USER_STATE_USER
        self.updated_at = datetime.now(timezone.utc)
        return self

    def deactivate(self) -> "User":
        """将用户状态设为封禁"""
        self.state = USER_STATE_BANNED
        self.updated_at = datetime.now(timezone.utc)
        return self

    def set_default_target(self, target_session: str) -> "User":
        """设置默认推送目标会话"""
        self.default_target_session = target_session
        self.needs_binding_notice = 0
        self.updated_at = datetime.now(timezone.utc)
        return self

    def mark_binding_notice(self) -> "User":
        """标记需要绑定通知"""
        self.needs_binding_notice = 1
        self.updated_at = datetime.now(timezone.utc)
        return self

    def consume_binding_notice(self) -> bool:
        """消费绑定通知标记"""
        if self.needs_binding_notice == 0:
            return False
        self.needs_binding_notice = 0
        self.updated_at = datetime.now(timezone.utc)
        return True

    def get_effective_option(self, key: str) -> int | str | None:
        """
        获取生效的配置选项值

        Args:
            key: 选项名称

        Returns:
            选项值，如果未设置则返回None
        """
        if hasattr(self, key):
            value = getattr(self, key)
            if value != INHERIT_VALUE:
                return value
        return None

    def get_effective_handlers(self) -> list[dict[str, Any]]:
        """获取规范化后的 handlers。"""
        return self.get_handlers()
