"""ai_comment 管道/直连双模式内容处理器测试。

- 管道模式（ai_comment_pipeline=True，默认）：不调 LLM、不读图，只构造 comment_trigger。
- 直连模式（ai_comment_pipeline=False）：v2.5.0 行为，直连 text_chat 生成正文。
- ai_filter 拦截：comment_trigger 为空（需求 2：跳过即不读图不评论）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrbot_plugin_rsshub.src.application.services.content_handlers import (
    ContentHandlerRuntime,
    EntryContentContext,
)
from astrbot_plugin_rsshub.src.domain.entities.subscription import Subscription
from astrbot_plugin_rsshub.src.infrastructure.config import ContentHandlerSettings


class FakeProviderResponse:
    def __init__(self, completion_text: str) -> None:
        self.completion_text = completion_text


class FakeProvider:
    def __init__(self, completion_text: str) -> None:
        self.completion_text = completion_text
        self.prompts = []

    async def text_chat(self, **kwargs):
        self.prompts.append(kwargs)
        return FakeProviderResponse(self.completion_text)

    def meta(self):
        return SimpleNamespace(id="fake-provider")


class FakeContext:
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider

    def get_using_provider(self, session_id=None):
        return self.provider


def _comment_subscription(
    handlers=None,
    **overrides,
) -> Subscription:
    values = dict(
        id=1,
        user_id="user-1",
        feed_id=10,
        platform_name="telegram",
        target_session="telegram:GroupMessage:1",
        handlers_mode="override",
        handlers=handlers
        or [
            {
                "id": "builtin.ai_comment.default",
                "type": "builtin",
                "name": "ai_comment",
                "status": 1,
                "config": {"prompt": "吐槽一下", "with_media": False},
            }
        ],
    )
    values.update(overrides)
    return Subscription(**values)


def _entry(**overrides) -> EntryContentContext:
    values = dict(
        title="标题",
        summary="摘要",
        content="正文",
        link="https://example.com/entry",
        author="作者",
        feed_title="Feed",
        feed_link="https://example.com/feed.xml",
    )
    values.update(overrides)
    return EntryContentContext(**values)


def _runtime(provider: FakeProvider, *, pipeline_mode: bool) -> ContentHandlerRuntime:
    return ContentHandlerRuntime(
        FakeContext(provider),
        settings=ContentHandlerSettings(ai_comment_pipeline=pipeline_mode),
    )


@pytest.mark.asyncio
async def test_pipeline_mode_ai_comment_skips_llm_and_builds_trigger():
    provider = FakeProvider("管道模式不该直连")
    runtime = _runtime(provider, pipeline_mode=True)

    result = await runtime.process_entry_with_trace(
        subscription=_comment_subscription(),
        user=None,
        entry=_entry(),
        session_id="session-1",
    )

    # 不调 LLM、不读图、不生成正文
    assert provider.prompts == []
    assert result.commentary == ""
    assert result.comment_trigger is not None
    assert result.comment_trigger.prompt == "吐槽一下"
    assert result.comment_trigger.with_media is False
    assert result.comment_trigger.config == {
        "prompt": "吐槽一下",
        "with_media": False,
    }
    # 原文条目被带进 trigger（评论基于改写前的原文）
    assert result.comment_trigger.entry.title == "标题"

    trace = [t for t in result.trace if t["name"] == "ai_comment"]
    assert trace
    assert trace[0]["mode"] == "pipeline"
    assert trace[0]["with_media"] is False
    assert trace[0]["prompt_present"] is True
    assert "commentary_present" not in trace[0]


@pytest.mark.asyncio
async def test_pipeline_mode_with_media_does_not_read_images():
    provider = FakeProvider("管道模式不该直连")
    runtime = _runtime(provider, pipeline_mode=True)
    sub = _comment_subscription(
        handlers=[
            {
                "id": "builtin.ai_comment.default",
                "type": "builtin",
                "name": "ai_comment",
                "status": 1,
                "config": {"prompt": "吐槽一下", "with_media": True},
            }
        ]
    )

    result = await runtime.process_entry_with_trace(
        subscription=sub,
        user=None,
        entry=_entry(media_items=(("image", "https://example.com/cat.jpg"),)),
        session_id="session-1",
    )

    # 图片由管道以原生图片组件读取，插件不调图片描述 provider
    assert provider.prompts == []
    assert result.comment_trigger is not None
    assert result.comment_trigger.with_media is True
    trace = [t for t in result.trace if t["name"] == "ai_comment"]
    assert trace[0]["with_media"] is True
    assert "images_read" not in trace[0]


@pytest.mark.asyncio
async def test_pipeline_mode_empty_prompt_handler_is_dropped():
    """ai_comment 的 prompt 为必填：空 prompt 的 handler 在归一化时被丢弃，
    管道模式不产生 trigger，也不产生空评论。"""
    provider = FakeProvider("管道模式不该直连")
    runtime = _runtime(provider, pipeline_mode=True)
    sub = _comment_subscription(
        handlers=[
            {
                "id": "builtin.ai_comment.default",
                "type": "builtin",
                "name": "ai_comment",
                "status": 1,
                "config": {"prompt": "", "with_media": False},
            }
        ]
    )

    result = await runtime.process_entry_with_trace(
        subscription=sub,
        user=None,
        entry=_entry(),
        session_id="session-1",
    )

    assert provider.prompts == []
    assert result.comment_trigger is None
    names = [t["name"] for t in result.trace]
    assert "ai_comment" not in names


@pytest.mark.asyncio
async def test_direct_mode_preserves_text_chat_behavior():
    provider = FakeProvider("这条推送真有意思")
    runtime = _runtime(provider, pipeline_mode=False)

    result = await runtime.process_entry_with_trace(
        subscription=_comment_subscription(),
        user=None,
        entry=_entry(),
        session_id="session-1",
    )

    # v2.5.0 行为：直连 text_chat 生成正文
    assert len(provider.prompts) == 1
    assert "吐槽一下" in provider.prompts[0]["prompt"]
    assert result.commentary == "这条推送真有意思"
    assert result.comment_trigger is None

    trace = [t for t in result.trace if t["name"] == "ai_comment"]
    assert trace[0]["commentary_present"] is True
    assert trace[0]["commentary_length"] == len("这条推送真有意思")
    assert "mode" not in trace[0]


@pytest.mark.asyncio
async def test_direct_mode_marks_fallback_provider_in_trace():
    """主 provider 限流/失败后评论由回退 provider 生成时，trace 与日志
    显式记录该事实（v2.6.0 首次推送日志暴露的质量劣化不可察觉问题）。"""
    from unittest.mock import AsyncMock, patch

    provider = FakeProvider("主 provider 不该被调到")
    runtime = _runtime(provider, pipeline_mode=False)

    with patch.object(
        ContentHandlerRuntime,
        "_chat_with_backoff",
        new_callable=AsyncMock,
        return_value=("兜底评论", "deepseek/deepseek-v4-flash"),
    ):
        result = await runtime.process_entry_with_trace(
            subscription=_comment_subscription(),
            user=None,
            entry=_entry(),
            session_id="session-1",
        )

    assert result.commentary == "兜底评论"
    trace = [t for t in result.trace if t["name"] == "ai_comment"]
    assert trace[0]["provider_fallback"] is True
    assert trace[0]["model_id"] == "deepseek/deepseek-v4-flash"


@pytest.mark.asyncio
async def test_pipeline_mode_ai_filter_block_clears_trigger():
    provider = FakeProvider('{"allow":false,"reason":"广告"}')
    runtime = _runtime(provider, pipeline_mode=True)
    sub = _comment_subscription(
        handlers=[
            {
                "id": "builtin.ai_filter.default",
                "type": "builtin",
                "name": "ai_filter",
                "status": 1,
                "config": {"prompt": "跳过广告", "input_scope": "text"},
            },
            {
                "id": "builtin.ai_comment.default",
                "type": "builtin",
                "name": "ai_comment",
                "status": 1,
                "config": {"prompt": "吐槽一下", "with_media": True},
            },
        ]
    )

    result = await runtime.process_entry_with_trace(
        subscription=sub,
        user=None,
        entry=_entry(),
        session_id="session-1",
    )

    # ai_filter 早退：不构造 trigger，也不读图
    assert result.allow is False
    assert result.reason == "广告"
    assert result.comment_trigger is None
    names = [t["name"] for t in result.trace]
    assert "ai_comment" not in names


# ---- merge_condition（合并转发条件，纯本地规则判断）----


def _merge_subscription(config: dict | None = None) -> Subscription:
    return _comment_subscription(
        handlers=[
            {
                "id": "builtin.merge_condition.default",
                "type": "builtin",
                "name": "merge_condition",
                "status": 1,
                "config": config or {"max_chars": 80, "max_images": 1},
            }
        ]
    )


def test_merge_condition_short_text_few_images_direct_send():
    entry = _entry(
        content="短",
        media_items=(("image", "https://example.com/1.jpg"),),
        media_urls=("https://example.com/1.jpg",),
    )
    direct_send, trace = ContentHandlerRuntime._run_merge_condition(
        entry, {"max_chars": 80, "max_images": 1}
    )
    assert direct_send is True
    assert trace["text_len"] == len("标题") + len("短")
    assert trace["image_count"] == 1
    assert trace["has_rich_media"] is False


def test_merge_condition_long_text_stays_forward():
    entry = _entry(content="长" * 100)
    direct_send, _ = ContentHandlerRuntime._run_merge_condition(
        entry, {"max_chars": 80, "max_images": 1}
    )
    assert direct_send is False


def test_merge_condition_many_images_stays_forward():
    entry = _entry(
        media_items=(
            ("image", "https://example.com/1.jpg"),
            ("image", "https://example.com/2.jpg"),
        ),
    )
    direct_send, trace = ContentHandlerRuntime._run_merge_condition(
        entry, {"max_chars": 80, "max_images": 1}
    )
    assert direct_send is False
    assert trace["image_count"] == 2


def test_merge_condition_video_stays_forward():
    entry = _entry(
        content="短",
        media_items=(("video", "https://example.com/1.mp4"),),
    )
    direct_send, trace = ContentHandlerRuntime._run_merge_condition(
        entry, {"max_chars": 80, "max_images": 1}
    )
    assert direct_send is False
    assert trace["has_rich_media"] is True


def test_merge_condition_defaults_when_config_missing():
    # 未配置 config 时回落默认 max_chars=80 / max_images=1
    entry = _entry(content="短", media_items=(("image", "https://example.com/1.jpg"),))
    direct_send, trace = ContentHandlerRuntime._run_merge_condition(entry, {})
    assert direct_send is True
    assert trace["max_chars"] == 80
    assert trace["max_images"] == 1


def test_merge_condition_falls_back_on_invalid_thresholds():
    entry = _entry(content="短", media_items=(("image", "https://example.com/1.jpg"),))
    direct_send, _ = ContentHandlerRuntime._run_merge_condition(
        entry, {"max_chars": "not-a-number", "max_images": "x"}
    )
    assert direct_send is True


@pytest.mark.asyncio
async def test_merge_condition_in_chain_sets_direct_send():
    runtime = _runtime(FakeProvider(""), pipeline_mode=True)
    sub = _merge_subscription()
    entry = _entry(
        content="短",
        media_items=(("image", "https://example.com/1.jpg"),),
        media_urls=("https://example.com/1.jpg",),
    )
    result = await runtime.process_entry_with_trace(
        subscription=sub,
        user=None,
        entry=entry,
        session_id="session-1",
    )
    assert result.direct_send is True
    names = [t["name"] for t in result.trace]
    assert "merge_condition" in names


@pytest.mark.asyncio
async def test_merge_condition_absent_direct_send_none():
    runtime = _runtime(FakeProvider(""), pipeline_mode=True)
    sub = _comment_subscription()  # 只有 ai_comment，无 merge_condition
    result = await runtime.process_entry_with_trace(
        subscription=sub,
        user=None,
        entry=_entry(content="短"),
        session_id="session-1",
    )
    assert result.direct_send is None

