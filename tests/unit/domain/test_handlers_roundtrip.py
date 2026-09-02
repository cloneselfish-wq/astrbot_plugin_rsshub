"""handlers 归一化/序列化幂等性回归测试。

覆盖 pre-existing bug：`dump_handlers(normalize_handlers(x))` 双重 normalize
导致 Subscription/User 的 `set_handlers` 与 `handler_specs=` 构造静默丢 handler。
"""

from __future__ import annotations

from astrbot_plugin_rsshub.src.domain.entities.handlers import (
    HandlerSpec,
    dump_handlers,
    normalize_handlers,
)
from astrbot_plugin_rsshub.src.domain.entities.subscription import Subscription
from astrbot_plugin_rsshub.src.domain.entities.user import User

AI_FILTER = {
    "id": "builtin.ai_filter.default",
    "type": "builtin",
    "name": "ai_filter",
    "status": 1,
    "config": {"prompt": "keep important"},
}


def test_normalize_handlers_is_idempotent_on_spec_list():
    specs = normalize_handlers([AI_FILTER])
    assert len(specs) == 1
    assert isinstance(specs[0], HandlerSpec)

    # 对已 normalize 的 list[HandlerSpec] 再次 normalize 不得丢数据
    again = normalize_handlers(specs)
    assert [s.id for s in again] == ["builtin.ai_filter.default"]


def test_dump_handlers_after_normalize_does_not_drop():
    # 核心回归：双重 normalize 曾返回 []，现应保留完整 handler
    result = dump_handlers(normalize_handlers([AI_FILTER]))
    assert len(result) == 1
    assert result[0]["id"] == "builtin.ai_filter.default"


def test_subscription_set_handlers_preserves_handlers():
    sub = Subscription(user_id="u1", feed_id=1)
    sub.set_handlers([AI_FILTER])
    handlers = sub.get_handlers()
    assert [h["name"] for h in handlers] == ["ai_filter"]


def test_subscription_construct_with_handler_specs_preserves_handlers():
    sub = Subscription(user_id="u1", feed_id=1, handler_specs=[AI_FILTER])
    assert [h["name"] for h in sub.get_handlers()] == ["ai_filter"]


def test_subscription_construct_with_handlers_alias_preserves_handlers():
    sub = Subscription(user_id="u1", feed_id=1, handlers=[AI_FILTER])
    assert [h["name"] for h in sub.get_handlers()] == ["ai_filter"]


def test_subscription_clear_handlers_empties():
    sub = Subscription(user_id="u1", feed_id=1)
    sub.set_handlers([AI_FILTER])
    sub.clear_handlers()
    assert sub.get_handlers() == []


def test_user_set_handlers_preserves_handlers():
    user = User(id="u1")
    user.set_handlers([AI_FILTER])
    assert [h["name"] for h in user.get_handlers()] == ["ai_filter"]


def test_user_construct_with_handler_specs_preserves_handlers():
    user = User(id="u1", handler_specs=[AI_FILTER])
    assert [h["name"] for h in user.get_handlers()] == ["ai_filter"]
