from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from astrbot_plugin_rsshub.src.domain.entities.content_types import LayoutFragment
from astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender import (
    OneBotMessageSender,
)
from astrbot_plugin_rsshub.src.infrastructure.messaging.senders.types import (
    ChannelInfo,
    MessageContext,
    PreparedMedia,
    SendRequest,
    SendResult,
)


class _Plain:
    def __init__(self, text: str) -> None:
        self.text = text


class _Node:
    def __init__(self, content: list, name: str, uin: str = "0") -> None:
        self.content = content
        self.name = name
        self.uin = uin


class _Nodes:
    def __init__(self, nodes: list) -> None:
        self.nodes = nodes


class _Image:
    def __init__(self, file: str) -> None:
        self.file = file


class _Video:
    def __init__(self, file: str) -> None:
        self.file = file


class _Record:
    def __init__(self, file: str, text: str = "") -> None:
        self.file = file
        self.text = text


class _File:
    def __init__(self, name: str, file: str, url: str) -> None:
        self.name = name
        self.file = file
        self.url = url


@pytest.mark.asyncio
async def test_onebot_sender_falls_back_to_text_nodes_when_merged_forward_fails(
    monkeypatch,
):
    sender = OneBotMessageSender()
    calls: list[tuple[str, list]] = []

    async def fake_send_chain(session_id: str, chain: list, **_kwargs):
        calls.append((session_id, chain))
        if len(calls) == 1:
            return SendResult(ok=False, detail="forward failed")
        return SendResult(ok=True)

    monkeypatch.setattr(sender, "_send_chain", fake_send_chain)
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Node",
        _Node,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Nodes",
        _Nodes,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Plain",
        _Plain,
    )
    monkeypatch.setattr(
        sys.modules["astrbot.api.message_components"], "Image", _Image, raising=False
    )
    monkeypatch.setattr(
        sys.modules["astrbot.api.message_components"], "Video", _Video, raising=False
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.get_bot_self_id",
        lambda _platform_name: "123456789",
    )

    request = SendRequest(
        session_id="default:GroupMessage:1",
        message="entry content",
        prepared_media=[
            PreparedMedia(
                media_type="video",
                original_url="https://example.com/video.mp4",
                local_path=Path("/tmp/video.mp4"),
            ),
            PreparedMedia(
                media_type="image",
                original_url="https://example.com/image.jpg",
                local_path=Path("/tmp/image.jpg"),
            ),
        ],
    )

    result = await sender.send_to_user(
        request,
        context=MessageContext(
            channel=ChannelInfo(title="Feed Title"),
            platform_name="aiocqhttp",
        ),
    )

    assert result.ok is True
    assert len(calls) == 2
    fallback_nodes = calls[1][1][0].nodes
    assert len(fallback_nodes) == 1
    assert fallback_nodes[0].content[0].text.startswith("entry content")
    assert "https://example.com/video.mp4" in fallback_nodes[0].content[0].text
    assert "https://example.com/image.jpg" in fallback_nodes[0].content[0].text


@pytest.mark.asyncio
async def test_onebot_sender_places_media_nodes_before_text(monkeypatch):
    sender = OneBotMessageSender()
    calls: list[tuple[str, list]] = []

    async def fake_send_chain(session_id: str, chain: list, **_kwargs):
        calls.append((session_id, chain))
        return SendResult(ok=True)

    monkeypatch.setattr(sender, "_send_chain", fake_send_chain)
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Node",
        _Node,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Nodes",
        _Nodes,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Plain",
        _Plain,
    )
    monkeypatch.setattr(
        sys.modules["astrbot.api.message_components"], "Image", _Image, raising=False
    )
    monkeypatch.setattr(
        sys.modules["astrbot.api.message_components"], "Video", _Video, raising=False
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.get_bot_self_id",
        lambda _platform_name: "123456789",
    )

    request = SendRequest(
        session_id="default:GroupMessage:1",
        message="entry content",
        prepared_media=[
            PreparedMedia(
                media_type="image",
                original_url="https://example.com/image.jpg",
                local_path=Path("/tmp/image.jpg"),
            ),
            PreparedMedia(
                media_type="video",
                original_url="https://example.com/video.mp4",
                local_path=Path("/tmp/video.mp4"),
            ),
        ],
    )

    result = await sender.send_to_user(
        request,
        context=MessageContext(
            channel=ChannelInfo(title="Feed Title"),
            platform_name="aiocqhttp",
        ),
    )

    assert result.ok is True
    nodes = calls[0][1][0].nodes
    assert isinstance(nodes[0].content[0], _Image)
    assert isinstance(nodes[1].content[0], _Video)
    assert isinstance(nodes[2].content[0], _Plain)
    assert nodes[2].content[0].text == "entry content"


@pytest.mark.asyncio
async def test_onebot_sender_prefers_local_video_path_by_default(monkeypatch):
    sender = OneBotMessageSender()
    calls: list[tuple[str, list]] = []

    async def fake_send_chain(session_id: str, chain: list, **_kwargs):
        calls.append((session_id, chain))
        return SendResult(ok=True)

    monkeypatch.setattr(sender, "_send_chain", fake_send_chain)
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Node",
        _Node,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Nodes",
        _Nodes,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Plain",
        _Plain,
    )
    monkeypatch.setattr(
        sys.modules["astrbot.api.message_components"], "Image", _Image, raising=False
    )
    monkeypatch.setattr(
        sys.modules["astrbot.api.message_components"], "Video", _Video, raising=False
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.get_bot_self_id",
        lambda _platform_name: "123456789",
    )

    request = SendRequest(
        session_id="default:GroupMessage:1",
        message="entry content",
        prepared_media=[
            PreparedMedia(
                media_type="video",
                original_url="https://example.com/video.mp4",
                local_path=Path("/tmp/video.mp4"),
            ),
        ],
    )

    result = await sender.send_to_user(
        request,
        context=MessageContext(
            channel=ChannelInfo(title="Feed Title"),
            platform_name="aiocqhttp",
        ),
    )

    assert result.ok is True
    assert len(calls) == 1
    media_node = calls[0][1][0].nodes[0]
    assert media_node.content[0].file == "/tmp/video.mp4"


@pytest.mark.asyncio
async def test_onebot_sender_video_uses_local_file(
    monkeypatch,
):
    sender = OneBotMessageSender()
    calls: list[tuple[str, list]] = []

    async def fake_send_chain(session_id: str, chain: list, **_kwargs):
        calls.append((session_id, chain))
        return SendResult(ok=True)

    monkeypatch.setattr(sender, "_send_chain", fake_send_chain)
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Node",
        _Node,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Nodes",
        _Nodes,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Plain",
        _Plain,
    )
    monkeypatch.setattr(
        sys.modules["astrbot.api.message_components"], "Video", _Video, raising=False
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.get_bot_self_id",
        lambda _platform_name: "123456789",
    )

    result = await sender.send_to_user(
        SendRequest(
            session_id="default:GroupMessage:1",
            message="entry content",
            prepared_media=[
                PreparedMedia(
                    media_type="video",
                    original_url="https://example.com/video.mp4",
                    local_path=Path("/tmp/video.mp4"),
                )
            ],
        ),
        context=MessageContext(
            channel=ChannelInfo(title="Feed Title"),
            platform_name="aiocqhttp",
        ),
    )

    assert result.ok is True
    media_node = calls[0][1][0].nodes[0]
    assert media_node.content[0].file == "/tmp/video.mp4"


@pytest.mark.asyncio
async def test_onebot_sender_ignores_telegraph_strategy(monkeypatch):
    sender = OneBotMessageSender()
    calls: list[tuple[str, list]] = []

    async def fake_send_chain(session_id: str, chain: list, **_kwargs):
        calls.append((session_id, chain))
        return SendResult(ok=True)

    monkeypatch.setattr(sender, "_send_chain", fake_send_chain)
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Node",
        _Node,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Nodes",
        _Nodes,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Plain",
        _Plain,
    )
    monkeypatch.setattr(
        sys.modules["astrbot.api.message_components"], "Image", _Image, raising=False
    )
    monkeypatch.setattr(
        sys.modules["astrbot.api.message_components"], "Video", _Video, raising=False
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.get_bot_self_id",
        lambda _platform_name: "123456789",
    )

    result = await sender.send_to_user(
        SendRequest(
            session_id="default:GroupMessage:1",
            message="entry content",
            prepared_media=[
                PreparedMedia(
                    media_type="image",
                    original_url="https://example.com/1.jpg",
                    local_path=Path("/tmp/1.jpg"),
                ),
                PreparedMedia(
                    media_type="image",
                    original_url="https://example.com/2.jpg",
                    local_path=Path("/tmp/2.jpg"),
                ),
            ],
        ),
        context=MessageContext(
            channel=ChannelInfo(title="Feed Title"),
            platform_name="aiocqhttp",
            send_mode=0,
            sender_strategy={
                "enable_telegraph": True,
                "telegraph_token": "ignored-token",
            },
        ),
    )

    assert result.ok is True
    assert len(calls) == 1
    nodes = calls[0][1][0].nodes
    assert len(nodes) == 3
    text_nodes = [
        node.content[0].text for node in nodes if isinstance(node.content[0], _Plain)
    ]
    assert text_nodes == ["entry content"]
    assert all("Telegraph:" not in text for text in text_nodes)


@pytest.mark.asyncio
async def test_onebot_original_style_sends_layout_without_merged_forward(monkeypatch):
    sender = OneBotMessageSender()
    calls: list[tuple[str, list]] = []

    async def fake_send_chain(session_id: str, chain: list, **_kwargs):
        calls.append((session_id, chain))
        return SendResult(ok=True)

    monkeypatch.setattr(sender, "_send_chain", fake_send_chain)
    monkeypatch.setattr(
        sys.modules["astrbot.api.message_components"], "Plain", _Plain, raising=False
    )
    monkeypatch.setattr(
        sys.modules["astrbot.api.message_components"], "Image", _Image, raising=False
    )

    result = await sender.send_to_user(
        SendRequest(
            session_id="default:GroupMessage:1",
            message="fallback text",
            layout=[
                LayoutFragment(
                    kind="image",
                    media_type="image",
                    url="https://example.com/1.jpg",
                ),
                LayoutFragment(kind="text", text="caption 1"),
                LayoutFragment(
                    kind="image",
                    media_type="image",
                    url="https://example.com/2.jpg",
                ),
                LayoutFragment(kind="text", text="caption 2"),
            ],
        ),
        context=MessageContext(
            channel=ChannelInfo(title="Feed Title"),
            platform_name="aiocqhttp",
            style=2,
        ),
    )

    assert result.ok is True
    assert len(calls) == 2
    assert isinstance(calls[0][1][0], _Image)
    assert isinstance(calls[0][1][1], _Plain)
    assert calls[0][1][1].text == "caption 1"
    assert isinstance(calls[1][1][0], _Image)
    assert isinstance(calls[1][1][1], _Plain)
    assert calls[1][1][1].text == "caption 2"


@pytest.mark.asyncio
async def test_onebot_sender_uses_event_self_id_for_forward_nodes(monkeypatch):
    """命令响应场景应把事件中的 bot QQ 号写入转发节点。"""
    sender = OneBotMessageSender()
    calls: list[tuple[str, list]] = []

    async def fake_send_chain(session_id: str, chain: list, **_kwargs):
        calls.append((session_id, chain))
        return SendResult(ok=True)

    monkeypatch.setattr(sender, "_send_chain", fake_send_chain)
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Node",
        _Node,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Nodes",
        _Nodes,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Plain",
        _Plain,
    )

    result = await sender.send_to_user(
        SendRequest(session_id="default:GroupMessage:1", message="entry content"),
        context=MessageContext(
            channel=ChannelInfo(title="Feed Title"),
            platform_name="aiocqhttp",
            event=SimpleNamespace(message_obj=SimpleNamespace(self_id="123456789")),
        ),
    )

    assert result.ok is True
    assert calls[0][1][0].nodes[0].uin == "123456789"


@pytest.mark.asyncio
async def test_onebot_sender_uses_provider_self_id_for_active_push(monkeypatch):
    """主动推送应使用 provider 解析到的唯一 bot QQ 号。"""
    sender = OneBotMessageSender()
    calls: list[tuple[str, list]] = []

    async def fake_send_chain(session_id: str, chain: list, **_kwargs):
        calls.append((session_id, chain))
        return SendResult(ok=True)

    monkeypatch.setattr(sender, "_send_chain", fake_send_chain)
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Node",
        _Node,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Nodes",
        _Nodes,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Plain",
        _Plain,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.get_bot_self_id",
        lambda _platform_name: "987654321",
    )

    result = await sender.send_to_user(
        SendRequest(session_id="default:GroupMessage:1", message="entry content"),
        context=MessageContext(
            channel=ChannelInfo(title="Feed Title"),
            platform_name="aiocqhttp",
        ),
    )

    assert result.ok is True
    assert calls[0][1][0].nodes[0].uin == "987654321"


@pytest.mark.asyncio
async def test_onebot_sender_uses_stored_bot_self_id_for_active_push(monkeypatch):
    """主动推送（无事件）时，应优先使用订阅创建时存储的 bot QQ 号，
    让合并转发节点显示为订阅时那个 bot（如 bot3），而不是 provider
    解析到的任意一个账号。"""
    sender = OneBotMessageSender()
    calls: list[tuple[str, list]] = []

    async def fake_send_chain(session_id: str, chain: list, **_kwargs):
        calls.append((session_id, chain))
        return SendResult(ok=True)

    monkeypatch.setattr(sender, "_send_chain", fake_send_chain)
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Node",
        _Node,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Nodes",
        _Nodes,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Plain",
        _Plain,
    )
    # provider 可能解析到任意账号（如 bot1），存储的 bot_self_id 必须优先
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.get_bot_self_id",
        lambda _platform_name: "123456789",
    )

    result = await sender.send_to_user(
        SendRequest(session_id="default:GroupMessage:1", message="entry content"),
        context=MessageContext(
            channel=ChannelInfo(title="Feed Title"),
            platform_name="aiocqhttp",
            bot_self_id="bot3",
        ),
    )

    assert result.ok is True
    assert calls[0][1][0].nodes[0].uin == "bot3"


@pytest.mark.asyncio
async def test_onebot_sender_falls_back_to_base_sender_when_self_id_is_unknown(monkeypatch):
    """多 bot 同平台无法确定 self_id 时，退回到基类纯文本/图片组件发送，
    避免合并转发节点携带错误的 uin 导致消息显示为从其他 bot "伪造转发"。"""
    sender = OneBotMessageSender()
    calls: list[tuple[str, list]] = []

    async def fake_send_chain(session_id: str, chain: list, **_kwargs):
        calls.append((session_id, chain))
        return SendResult(ok=True)

    monkeypatch.setattr(sender, "_send_chain", fake_send_chain)
    # Mock get_bot_self_id to return empty — simulates multi-bot same-platform
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.get_bot_self_id",
        lambda _platform_name: "",
    )
    # Mock _formatter.build_chain so base sender produces a predictable chain
    monkeypatch.setattr(
        sender._formatter,
        "build_chain",
        lambda prepared_media, text, failed_urls, platform: [_Plain(text or "fallback")],
    )

    result = await sender.send_to_user(
        SendRequest(session_id="default:GroupMessage:1", message="entry content"),
        context=MessageContext(
            channel=ChannelInfo(title="Feed Title"),
            platform_name="aiocqhttp",
        ),
    )

    assert result.ok is True
    # 基类发送路径：chain 是纯组件列表，不包含合并转发 Nodes
    assert len(calls) == 1
    chain = calls[0][1]
    assert len(chain) == 1
    assert isinstance(chain[0], _Plain)
    assert chain[0].text == "entry content"


# ------------------------------------------------------------------
# GIF conversion regression
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_onebot_merged_forward_uses_image_for_gif(monkeypatch):
    """OneBot 合并转发对 video + *.gif 应生成 Image(file=...)。"""
    sender = OneBotMessageSender()
    calls: list[tuple[str, list]] = []

    async def fake_send_chain(session_id: str, chain: list, **_kwargs):
        calls.append((session_id, chain))
        return SendResult(ok=True)

    monkeypatch.setattr(sender, "_send_chain", fake_send_chain)
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Node",
        _Node,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Nodes",
        _Nodes,
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.Plain",
        _Plain,
    )
    monkeypatch.setattr(
        sys.modules["astrbot.api.message_components"], "Image", _Image, raising=False
    )
    monkeypatch.setattr(
        sys.modules["astrbot.api.message_components"], "Video", _Video, raising=False
    )
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.get_bot_self_id",
        lambda _platform_name: "123456789",
    )

    result = await sender.send_to_user(
        SendRequest(
            session_id="default:GroupMessage:1",
            message="entry content",
            prepared_media=[
                PreparedMedia(
                    media_type="video",
                    original_url="https://example.com/video.mp4",
                    local_path=Path("/tmp/video.gif"),
                )
            ],
        ),
        context=MessageContext(
            channel=ChannelInfo(title="Feed Title"),
            platform_name="aiocqhttp",
        ),
    )

    assert result.ok is True
    nodes = calls[0][1][0].nodes
    assert len(nodes) == 2  # image node + text node
    assert isinstance(nodes[0].content[0], _Image)
    assert nodes[0].content[0].file == "/tmp/video.gif"


@pytest.mark.asyncio
async def test_onebot_original_style_gif_from_downloaded_media(monkeypatch):
    """original layout 使用真实预下载结果时，video→gif 应按 Image 发送。"""
    sender = OneBotMessageSender()
    calls: list[tuple[str, list]] = []

    async def fake_prepare_media(media, timeout=30, proxy=""):
        assert media == [("video", "https://example.com/video.mp4")]
        return [
            PreparedMedia(
                media_type="video",
                original_url="https://example.com/video.mp4",
                local_path=Path("/tmp/video.gif"),
            )
        ]

    async def fake_send_chain(session_id: str, chain: list, **_kwargs):
        calls.append((session_id, chain))
        return SendResult(ok=True)

    monkeypatch.setattr(sender, "prepare_media", fake_prepare_media)
    monkeypatch.setattr(sender, "_send_chain", fake_send_chain)
    monkeypatch.setattr(
        sys.modules["astrbot.api.message_components"], "Plain", _Plain, raising=False
    )
    monkeypatch.setattr(
        sys.modules["astrbot.api.message_components"], "Image", _Image, raising=False
    )
    monkeypatch.setattr(
        sys.modules["astrbot.api.message_components"], "Video", _Video, raising=False
    )

    result = await sender.send_to_user(
        SendRequest(
            session_id="default:GroupMessage:1",
            message="fallback",
            media=[("video", "https://example.com/video.mp4")],
            layout=[
                LayoutFragment(
                    kind="video",
                    media_type="video",
                    url="https://example.com/video.mp4",
                ),
                LayoutFragment(kind="text", text="caption"),
            ],
        ),
        context=MessageContext(platform_name="aiocqhttp", style=2),
    )

    assert result.ok is True
    assert len(calls) == 1
    assert isinstance(calls[0][1][0], _Image)
    assert calls[0][1][0].file == "/tmp/video.gif"
    assert isinstance(calls[0][1][1], _Plain)
    assert calls[0][1][1].text == "caption"


@pytest.mark.asyncio
async def test_onebot_plain_text_only_short_circuits_nodes(monkeypatch):
    """AI 评论场景 plain_text_only=True：即使有 bot_self_id 也不构造合并转发
    Nodes，走基类普通聊天文本发送（绝不进入伪造聊天记录）。"""
    sender = OneBotMessageSender()
    calls: list[tuple[str, list]] = []

    async def fake_send_chain(session_id: str, chain: list, **_kwargs):
        calls.append((session_id, chain))
        return SendResult(ok=True)

    monkeypatch.setattr(sender, "_send_chain", fake_send_chain)
    # 有确定 bot 号：普通路径会把纯文本也包成单节点合并转发
    monkeypatch.setattr(
        "astrbot_plugin_rsshub.src.infrastructure.messaging.senders.onebot_sender.get_bot_self_id",
        lambda _platform_name: "123456789",
    )
    # 基类发送路径：chain 是纯组件列表
    monkeypatch.setattr(
        sender._formatter,
        "build_chain",
        lambda prepared_media, text, failed_urls, platform: [
            _Plain(text or "fallback")
        ],
    )

    result = await sender.send_to_user(
        SendRequest(session_id="default:GroupMessage:1", message="bot 吐槽"),
        context=MessageContext(
            channel=ChannelInfo(title="Feed Title"),
            platform_name="aiocqhttp",
            plain_text_only=True,
        ),
    )

    assert result.ok is True
    assert len(calls) == 1
    chain = calls[0][1]
    assert len(chain) == 1
    assert isinstance(chain[0], _Plain)
    assert chain[0].text == "bot 吐槽"
