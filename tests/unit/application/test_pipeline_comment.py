"""PipelineCommentRouter 注入测试（astrbot 不在测试 venv，走轻量回退 + mock）。

覆盖：
- 群目标：At(bot_self_id) + Plain(message_str) + Image 组件、group_id=session_id、is_wake=True
- 好友目标：无 At、group_id=""
- with_media：图片先经插件下载器落本地（_download_comment_images），成功的
  以 Image.fromFileSystem 注入；下载失败/部分失败只注入成功的张数
- 会话解析失败 / 平台未找到 / StarTools 未就绪 / 群目标缺 bot_self_id → fallback=True
- 空 prompt 用默认口吻
- 合成消息标记：message_str 首行 SYNTHETIC_MESSAGE_TAG + raw_message 结构化标记
- message_id 带 rsshub-comment- 前缀（日志可追踪）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from astrbot_plugin_rsshub.src.application.services.content_handlers import (
    EntryContentContext,
)
from astrbot_plugin_rsshub.src.application.services.notification_dispatcher import (
    SendTarget,
)
from astrbot_plugin_rsshub.src.application.services.pipeline_comment import (
    SYNTHETIC_MESSAGE_TAG,
    At,
    DEFAULT_AI_COMMENT_PROMPT,
    Image,
    Plain,
    AiCommentTrigger,
    PipelineCommentRouter,
    StarTools,
)


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


def _target(session: str = "aiocqhttp:GroupMessage:123456", **overrides) -> SendTarget:
    values = dict(
        user_id="user-1",
        platform_name="aiocqhttp",
        target_session=session,
        sub_id=1,
        bot_self_id="bot-1",
    )
    values.update(overrides)
    return SendTarget(**values)


def _assert_injected(kwargs: dict, expect_at: bool, expect_images: int):
    components = kwargs["message"]
    index = 0
    if expect_at:
        assert isinstance(components[index], At)
        index += 1
    assert isinstance(components[index], Plain)
    index += 1
    images = components[index:]
    assert len(images) == expect_images
    assert all(isinstance(item, Image) for item in images)


def _patch_download(paths: list[Path]):
    return patch.object(
        PipelineCommentRouter,
        "_download_comment_images",
        new_callable=AsyncMock,
        return_value=paths,
    )


@pytest.mark.asyncio
async def test_inject_group_builds_at_plain_and_images():
    entry = _entry(
        media_items=(("image", "https://example.com/a.jpg"),),
        media_urls=("https://example.com/b.jpg",),
    )
    trigger = AiCommentTrigger(
        entry=entry, prompt="吐槽一下", with_media=True, config={}
    )
    router = PipelineCommentRouter(context=None)
    local_paths = [Path("/tmp/a.jpg"), Path("/tmp/b.jpg")]

    with (
        patch.object(StarTools, "create_message", new_callable=AsyncMock) as mock_msg,
        patch.object(StarTools, "create_event", new_callable=AsyncMock) as mock_evt,
        _patch_download(local_paths),
    ):
        result = await router.inject(_target(), trigger)

    assert result["ok"] is True
    assert result["fallback"] is False
    assert result["message_id"].startswith("rsshub-comment-")
    mock_msg.assert_awaited_once()
    kwargs = mock_msg.await_args.kwargs
    assert kwargs["type"] == "GroupMessage"
    assert kwargs["self_id"] == "bot-1"
    assert kwargs["session_id"] == "123456"
    assert kwargs["group_id"] == "123456"
    assert kwargs["message_id"] == result["message_id"]
    assert kwargs["sender"].user_id == "user-1"
    assert kwargs["sender"].nickname == "Feed"
    # raw_message 携带结构化合成消息标记（第三方插件可据此过滤）
    raw = kwargs["raw_message"]
    assert raw["source"] == "astrbot_plugin_rsshub"
    assert raw["kind"] == "ai_comment"
    assert raw["tag"] == SYNTHETIC_MESSAGE_TAG
    _assert_injected(kwargs, expect_at=True, expect_images=2)
    assert isinstance(kwargs["message"][0], At)
    assert kwargs["message"][0].qq == "bot-1"
    # 图片走本地路径（fromFileSystem），不再把原始 URL 交给核心下载。
    # mock 的 fromFileSystem 会 resolve()，故两边都用 resolve 后比较，平台无关。
    assert kwargs["message"][2].path == str(Path("/tmp/a.jpg").resolve(strict=False))
    assert kwargs["message"][2].file.startswith("file://")
    assert kwargs["message"][3].path == str(Path("/tmp/b.jpg").resolve(strict=False))
    message_str = kwargs["message"][1].text
    assert message_str.startswith(SYNTHETIC_MESSAGE_TAG)
    assert "标题：标题" in message_str
    assert "作者：作者" in message_str
    assert "来源：Feed" in message_str
    assert "链接：https://example.com/entry" in message_str
    assert "正文" in message_str
    assert "吐槽一下" in message_str
    # 图片提示与实际注入的 Image 组件数严格一致（2 张成功注入 → 提示 2 张）
    assert "本条消息附带 2 张条目图片" in message_str

    mock_evt.assert_awaited_once()
    call = mock_evt.await_args
    assert call.kwargs.get("platform") == "aiocqhttp"
    assert call.kwargs.get("is_wake") is True


@pytest.mark.asyncio
async def test_inject_friend_no_at_and_empty_group_id():
    trigger = AiCommentTrigger(
        entry=_entry(title="标题", content="正文", feed_title=""),
        prompt="",
        with_media=False,
        config={},
    )
    router = PipelineCommentRouter(context=None)

    with (
        patch.object(StarTools, "create_message", new_callable=AsyncMock) as mock_msg,
        patch.object(StarTools, "create_event", new_callable=AsyncMock),
    ):
        result = await router.inject(
            _target(session="telegram:FriendMessage:987654"), trigger
        )

    assert result["ok"] is True
    kwargs = mock_msg.await_args.kwargs
    assert kwargs["type"] == "FriendMessage"
    assert kwargs["group_id"] == ""
    # 私聊自动唤醒：无 At，只有 Plain
    _assert_injected(kwargs, expect_at=False, expect_images=0)
    message_str = kwargs["message"][0].text
    assert DEFAULT_AI_COMMENT_PROMPT in message_str
    assert "以 bot 自己的口吻" in message_str


@pytest.mark.asyncio
async def test_inject_with_media_filters_non_http_and_caps_images():
    trigger = AiCommentTrigger(
        entry=_entry(
            media_items=(
                ("image", "https://example.com/1.jpg"),
                ("image", "https://example.com/2.jpg"),
                ("image", "https://example.com/3.jpg"),
                ("image", "https://example.com/4.jpg"),
                ("image", "https://example.com/5.jpg"),
                ("image", "not-a-valid-url"),
                ("video", "https://example.com/v.mp4"),
            ),
        ),
        prompt="吐槽一下",
        with_media=True,
        config={},
    )
    router = PipelineCommentRouter(context=None)
    local_paths = [
        Path("/tmp/1.jpg"),
        Path("/tmp/2.jpg"),
        Path("/tmp/3.jpg"),
    ]

    with (
        patch.object(StarTools, "create_message", new_callable=AsyncMock) as mock_msg,
        patch.object(StarTools, "create_event", new_callable=AsyncMock),
        _patch_download(local_paths),
    ):
        result = await router.inject(_target(), trigger)

    assert result["ok"] is True
    kwargs = mock_msg.await_args.kwargs
    _assert_injected(kwargs, expect_at=True, expect_images=3)


@pytest.mark.asyncio
async def test_inject_dedupes_image_urls():
    trigger = AiCommentTrigger(
        entry=_entry(
            media_items=(("image", "https://example.com/dup.jpg"),),
            media_urls=("https://example.com/dup.jpg",),
        ),
        prompt="吐槽一下",
        with_media=True,
        config={},
    )
    router = PipelineCommentRouter(context=None)

    with (
        patch.object(StarTools, "create_message", new_callable=AsyncMock) as mock_msg,
        patch.object(StarTools, "create_event", new_callable=AsyncMock),
        _patch_download([Path("/tmp/dup.jpg")]),
    ):
        result = await router.inject(_target(), trigger)

    assert result["ok"] is True
    kwargs = mock_msg.await_args.kwargs
    _assert_injected(kwargs, expect_at=True, expect_images=1)


@pytest.mark.asyncio
async def test_inject_partial_image_download_failure_only_injects_successes():
    # 2 张收集到、1 张下载失败 → 只注入 1 张组件，避免 [图片] 占位失真
    trigger = AiCommentTrigger(
        entry=_entry(
            media_items=(
                ("image", "https://example.com/ok.jpg"),
                ("image", "https://example.com/broken.jpg"),
            ),
        ),
        prompt="吐槽一下",
        with_media=True,
        config={},
    )
    router = PipelineCommentRouter(context=None)

    with (
        patch.object(StarTools, "create_message", new_callable=AsyncMock) as mock_msg,
        patch.object(StarTools, "create_event", new_callable=AsyncMock),
        _patch_download([Path("/tmp/ok.jpg")]),
    ):
        result = await router.inject(_target(), trigger)

    assert result["ok"] is True
    kwargs = mock_msg.await_args.kwargs
    _assert_injected(kwargs, expect_at=True, expect_images=1)
    assert kwargs["message"][2].path == str(Path("/tmp/ok.jpg").resolve(strict=False))
    # 提示数量与实际注入组件数一致（1 张），不虚报
    assert "本条消息附带 1 张条目图片" in kwargs["message"][1].text


@pytest.mark.asyncio
async def test_inject_all_image_download_failure_injects_no_images():
    trigger = AiCommentTrigger(
        entry=_entry(
            media_items=(("image", "https://example.com/broken.jpg"),),
        ),
        prompt="吐槽一下",
        with_media=True,
        config={},
    )
    router = PipelineCommentRouter(context=None)

    with (
        patch.object(StarTools, "create_message", new_callable=AsyncMock) as mock_msg,
        patch.object(StarTools, "create_event", new_callable=AsyncMock),
        _patch_download([]),
    ):
        result = await router.inject(_target(), trigger)

    assert result["ok"] is True
    kwargs = mock_msg.await_args.kwargs
    _assert_injected(kwargs, expect_at=True, expect_images=0)
    # 全部下载失败：无图片组件，message_str 也不提及图片（不产生占位失真）
    assert "条目图片" not in kwargs["message"][1].text
    assert "[图片]" not in kwargs["message"][1].text


@pytest.mark.asyncio
async def test_inject_group_missing_bot_self_id_falls_back():
    # 群目标缺 bot_self_id：At("") 大概率过不了唤醒检查 → 直接回退直连，
    # 不再静默不发（v2.6.0 首次推送日志暴露的边缘问题）。
    trigger = AiCommentTrigger(entry=_entry(), prompt="吐槽一下", with_media=False, config={})
    router = PipelineCommentRouter(context=None)

    with (
        patch.object(StarTools, "create_message", new_callable=AsyncMock) as mock_msg,
        patch.object(StarTools, "create_event", new_callable=AsyncMock),
    ):
        result = await router.inject(_target(bot_self_id=""), trigger)

    assert result["ok"] is False
    assert result["fallback"] is True
    assert result["error"] == "missing bot_self_id"
    mock_msg.assert_not_awaited()


@pytest.mark.asyncio
async def test_inject_unparseable_session_falls_back():
    # MessageType("Group") 非法 → from_str 抛 ValueError → fallback
    trigger = AiCommentTrigger(entry=_entry(), prompt="吐槽一下", with_media=False, config={})
    router = PipelineCommentRouter(context=None)

    with (
        patch.object(StarTools, "create_message", new_callable=AsyncMock) as mock_msg,
        patch.object(StarTools, "create_event", new_callable=AsyncMock),
    ):
        result = await router.inject(_target(session="telegram:Group:1"), trigger)

    assert result == {
        "ok": False,
        "fallback": True,
        "error": "unparseable session",
        "message_id": "",
    }
    mock_msg.assert_not_awaited()


@pytest.mark.asyncio
async def test_inject_platform_not_found_falls_back():
    trigger = AiCommentTrigger(entry=_entry(), prompt="吐槽一下", with_media=False, config={})
    router = PipelineCommentRouter(context=None)

    with (
        patch.object(StarTools, "create_message", new_callable=AsyncMock) as mock_msg,
        patch.object(
            StarTools,
            "create_event",
            new_callable=AsyncMock,
            side_effect=ValueError("platform not found"),
        ),
    ):
        result = await router.inject(_target(), trigger)

    assert result["ok"] is False
    assert result["fallback"] is True
    assert "platform not found" in result["error"]
    assert result["message_id"] == ""


@pytest.mark.asyncio
async def test_inject_star_tools_not_ready_falls_back():
    trigger = AiCommentTrigger(entry=_entry(), prompt="吐槽一下", with_media=False, config={})
    router = PipelineCommentRouter(context=None)

    with (
        patch.object(
            StarTools,
            "create_message",
            new_callable=AsyncMock,
            side_effect=RuntimeError("StarTools not initialized"),
        ),
        patch.object(StarTools, "create_event", new_callable=AsyncMock),
    ):
        result = await router.inject(_target(), trigger)

    assert result["ok"] is False
    assert result["fallback"] is True
    assert "StarTools not initialized" in result["error"]


@pytest.mark.asyncio
async def test_inject_message_type_value_roundtrip():
    # OTHER_MESSAGE 也走正常注入（既非群也非好友，不加 At）
    trigger = AiCommentTrigger(entry=_entry(), prompt="", with_media=False, config={})
    router = PipelineCommentRouter(context=None)

    with (
        patch.object(StarTools, "create_message", new_callable=AsyncMock) as mock_msg,
        patch.object(StarTools, "create_event", new_callable=AsyncMock),
    ):
        result = await router.inject(
            _target(session="telegram:OtherMessage:abc"), trigger
        )

    assert result["ok"] is True
    kwargs = mock_msg.await_args.kwargs
    assert kwargs["type"] == "OtherMessage"
    assert kwargs["group_id"] == ""
    _assert_injected(kwargs, expect_at=False, expect_images=0)


def test_build_message_str_truncates_long_body():
    long_body = "甲" * 5000
    trigger = AiCommentTrigger(
        entry=_entry(title="标题", content=long_body),
        prompt="吐槽一下",
        with_media=False,
        config={},
    )
    router = PipelineCommentRouter(context=None)
    message_str = router._build_message_str(trigger)
    assert message_str.startswith(SYNTHETIC_MESSAGE_TAG)
    assert "标题：标题" in message_str
    assert "吐槽一下" in message_str
    assert "甲" * 2000 + "…" in message_str
    assert "甲" * 2001 not in message_str


@pytest.mark.asyncio
async def test_inject_empty_image_urls_skips_download():
    # with_media=True 但条目没有可注入图片：不应调用下载器
    trigger = AiCommentTrigger(
        entry=_entry(), prompt="吐槽一下", with_media=True, config={}
    )
    router = PipelineCommentRouter(context=None)

    with (
        patch.object(StarTools, "create_message", new_callable=AsyncMock) as mock_msg,
        patch.object(StarTools, "create_event", new_callable=AsyncMock),
        patch.object(
            PipelineCommentRouter,
            "_download_comment_images",
            new_callable=AsyncMock,
        ) as mock_download,
    ):
        result = await router.inject(_target(), trigger)

    assert result["ok"] is True
    mock_download.assert_awaited_once_with([])
    kwargs = mock_msg.await_args.kwargs
    _assert_injected(kwargs, expect_at=True, expect_images=0)
