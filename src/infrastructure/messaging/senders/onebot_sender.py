"""QQ OneBot 消息发送器

针对 QQ OneBot 协议的特定优化。
支持合并转发节点（Nodes）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from astrbot.api.message_components import Node, Nodes, Plain

from ...utils import get_logger
from ..napcat_stream import upload_file_stream
from .base_sender import DefaultMessageSender
from .types import MessageContext, SendRequest, SendResult, get_bot_self_id

if TYPE_CHECKING:
    pass

logger = get_logger()


class OneBotMessageSender(DefaultMessageSender):
    """QQ OneBot 平台消息发送器

    特性：
    - 合并转发节点（Nodes）：文字 + 图片/视频/音频/文件各自一个节点
    - 经典合并转发失败时回退为纯文本 Nodes；原始顺序排版不走合并转发
    """

    @staticmethod
    def _strategy_value(strategy, key: str, default=None):
        if strategy is None:
            return default
        if isinstance(strategy, dict):
            return strategy.get(key, default)
        return getattr(strategy, key, default)

    @classmethod
    def _napcat_stream_mode(cls, context: MessageContext | None) -> str:
        """获取 NapCat stream 模式配置

        Returns:
            "disabled", "fallback", 或 "always"
        """
        strategy = getattr(context, "sender_strategy", None) if context else None
        value = cls._strategy_value(strategy, "napcat_stream_mode", None)
        if value is None:
            return cls._get_onebot_napcat_stream_mode_default()
        return str(value)

    @classmethod
    def _get_onebot_napcat_stream_mode_default(cls) -> str:
        """获取 OneBot NapCat stream 模式的默认值"""
        from ....shared.constants import ONEBOT_NAPCAT_STREAM_MODE_DEFAULT

        return str(
            getattr(
                cls,
                "_onebot_napcat_stream_mode_default",
                ONEBOT_NAPCAT_STREAM_MODE_DEFAULT,
            )
        )

    @classmethod
    def _resolve_bot_client(cls, context: MessageContext | None) -> Any | None:
        """解析可用于 NapCat stream 的 bot 客户端

        优先使用消息事件携带的 bot（命令响应场景），
        否则通过全局 provider 按平台名解析（主动推送场景）。
        """
        event = getattr(context, "event", None) if context else None
        if event is not None:
            bot = getattr(event, "bot", None) or getattr(event, "_bot", None)
            if bot is not None:
                return bot

        from .types import get_bot_client

        platform_name = getattr(context, "platform_name", "") if context else ""
        return get_bot_client(platform_name or "")

    @classmethod
    def _resolve_bot_self_id(cls, context: MessageContext | None) -> str:
        """解析 bot 的 self_id（QQ 号），用于合并转发节点的 user_id。

        解析顺序：
        1. 命令响应场景：从事件消息对象的 ``self_id`` 读取（最可靠）。
        2. 主动推送场景：通过全局 provider 按 platform_name 解析（由
           bootstrap 注册，读取 CQHttp 的 ``_wsr_api_clients``）。
        3. 无法确认时返回空串。调用方保留 AstrBot SDK 的默认 ``uin="0"``，
           由兼容该缺省值的 OneBot 实现自行处理，避免伪造其他 QQ 号。
        """
        if context is not None:
            event = getattr(context, "event", None)
            if event is not None:
                msg_obj = getattr(event, "message_obj", None)
                if msg_obj is not None:
                    self_id = getattr(msg_obj, "self_id", None)
                    if self_id and str(self_id) != "0":
                        return str(self_id)
            # 订阅创建时存储的 bot self_id：主动推送（无事件）时用它来
            # 确定合并转发节点身份，让消息显示为订阅时那个 bot 发送。
            stored = getattr(context, "bot_self_id", "") or ""
            if stored and str(stored) != "0":
                return str(stored)
            platform_name = getattr(context, "platform_name", "") or ""
            if platform_name:
                self_id = get_bot_self_id(platform_name)
                if self_id and str(self_id) != "0":
                    return str(self_id)
        return ""

    @staticmethod
    def _build_forward_node(content: list, nickname: str, bot_self_id: str) -> Node:
        """构建合并转发节点，仅在已确认 bot QQ 号时显式设置 uin。"""
        if bot_self_id:
            return Node(content=content, name=nickname, uin=bot_self_id)
        return Node(content=content, name=nickname)

    async def send_to_user(
        self,
        request: SendRequest,
        context: MessageContext | None = None,
    ) -> SendResult:
        """发送合并转发消息到 QQ OneBot 用户

        经典策略下每条消息/媒体各自一个 Node；失败后回退为纯文本 Nodes。
        """
        if getattr(context, "plain_text_only", False):
            # AI 评论等场景：只按普通聊天文本发送，绝不构造合并转发节点。
            return await super().send_to_user(request, context)

        effective_prepared = None
        cleanup_owned = request.prepared_media is None
        try:
            session_id = request.session_id
            timeout = self._get_timeout_seconds()
            proxy = self._get_proxy()

            effective_prepared = request.prepared_media
            if effective_prepared is None and request.media:
                effective_prepared = await self.prepare_media(
                    request.media, timeout=timeout, proxy=proxy
                )
            effective_prepared = self._apply_generated_layout_local_paths(
                request,
                effective_prepared,
                mark_owned=cleanup_owned,
            )
            prepared_media_by_url = {
                pm.original_url: pm
                for pm in (effective_prepared or [])
                if pm.original_url
            }

            napcat_mode = self._napcat_stream_mode(context)

            if self._is_original_style(context) and request.layout:
                return await self._send_components_in_order(
                    session_id,
                    self._layout_to_components(
                        request, prepared_media_by_url=prepared_media_by_url
                    ),
                    combine_image_text=True,
                    default_text=request.message,
                    prepared_media_by_url=prepared_media_by_url,
                    platform="onebot",
                )

            bot_client = self._resolve_bot_client(context)
            bot_self_id = self._resolve_bot_self_id(context)

            # 多 bot 同平台时无法唯一确定发送 bot 的 self_id，
            # 此时退回到基类的纯文本/图片组件发送，避免合并转发节点
            # 携带错误的 uin 导致消息显示为从其他 bot "伪造转发"。
            if not bot_self_id:
                return await super().send_to_user(request, context)

            nickname = (
                context.channel.title if context and context.channel.title else "RSSHub"
            )

            from astrbot.api.message_components import File, Image, Record

            components = self._build_components(
                request,
                effective_prepared,
                context,
                failed_urls=[],
                platform="onebot",
            )
            components = self._apply_first_send_candidates(
                components,
                prepared_media_by_url,
                platform="onebot",
            )

            nodes: list[Node] = []
            # 摘出合并转发节点中的视频：NapCat 伪造合并转发不识别 video 节点的
            # 文件（即便已 stream 上传到 NapCat 本机仍报“识别URL失败”），
            # 因此视频改为在合并转发消息之外单独发送（见 _send_detached_videos），
            # 合并转发内保留文本/图片与原文链接。
            detached_videos = []
            for component in components:
                node_content: list | None = None
                if component.kind == "text":
                    node_content = [Plain(component.text or "RSS update")]
                elif component.kind == "media":
                    match component.media_type:
                        case "image":
                            node_content = [Image(file=component.file)]
                        case "video":
                            detached_videos.append(component)
                            continue
                elif component.kind == "tail":
                    match component.media_type:
                        case "audio":
                            node_content = [Record(file=component.file, text="audio")]
                        case "file":
                            node_content = [
                                File(
                                    name=component.name or "attachment",
                                    file=component.file,
                                    url=component.original_url,
                                )
                            ]
                if node_content:
                    nodes.append(
                        self._build_forward_node(node_content, nickname, bot_self_id)
                    )

            if not nodes and request.message:
                nodes.append(
                    self._build_forward_node(
                        [Plain(request.message)], nickname, bot_self_id
                    )
                )

            if not nodes:
                # 没有文本/图片节点但摘出了视频：直接单独发送视频即可
                if detached_videos:
                    video_failures = await self._send_detached_videos(
                        session_id, detached_videos, bot_client
                    )
                    return self._partial_send_result(video_failures)
                return SendResult(ok=False, detail="empty_message")

            # NapCat stream mode: always
            if napcat_mode == "always" and bot_client is not None:
                nodes = await self._stream_upload_nodes(bot_client, nodes)

            result = await self._send_chain(session_id, [Nodes(nodes)])

            # NapCat stream mode: fallback（合并转发内已不再包含视频节点，此分支
            # 实际不会触发，仅为兼容未来可能出现在节点内的可上传组件而保留）
            if (
                not result.ok
                and napcat_mode == "fallback"
                and bot_client is not None
                and self._has_local_video_nodes(nodes)
            ):
                logger.warning(
                    "OneBot send failed, trying NapCat stream fallback: session=%s",
                    session_id,
                )
                streamed_nodes = await self._stream_upload_nodes(bot_client, nodes)
                result = await self._send_chain(session_id, [Nodes(streamed_nodes)])

            if not result.ok:
                logger.warning(
                    "OneBot merged-forward send failed, fallback to text-only: "
                    "session=%s, detail=%s",
                    session_id,
                    result.detail,
                )
                failed_urls = self._formatter.collect_original_urls(
                    effective_prepared or []
                )
                fallback_message = (
                    self._message_with_all_generated_fallbacks(request) or "RSS update"
                )
                fallback_text = self._append_failed_links(
                    fallback_message,
                    failed_urls,
                )
                fallback_nodes = [
                    self._build_forward_node(
                        [Plain(fallback_text or "RSS update")],
                        nickname,
                        bot_self_id,
                    )
                ]
                send_result = await self._send_chain(
                    session_id, [Nodes(fallback_nodes)]
                )
            else:
                send_result = result

            # 合并转发消息之外，单独发送摘出的视频（与合并转发成败无关均尝试；
            # 无法直接送达时 _send_detached_videos 会补发可点击的原始视频链接，
            # 保证内容可达，故此处只记录降级、不视为整体失败）
            if detached_videos:
                video_failures = await self._send_detached_videos(
                    session_id, detached_videos, bot_client
                )
                if video_failures:
                    logger.warning(
                        "OneBot detached video degraded: session=%s, failures=%d",
                        session_id,
                        len(video_failures),
                    )
            return send_result

        except Exception as err:
            logger.error(
                "OneBot merged-forward send exception: session=%s, err=%s",
                request.session_id,
                err,
                exc_info=True,
            )
            return SendResult(
                ok=False,
                transient=self._is_transient_network_error(err),
                detail=self._normalize_error_detail(str(err)),
            )
        finally:
            if cleanup_owned:
                self._cleanup_owned_paths(effective_prepared)

    async def _send_detached_videos(
        self,
        session_id: str,
        video_components: list,
        bot_client: Any | None,
    ) -> list[SendResult]:
        """在合并转发消息之外，单独发送从合并转发节点中摘出的视频。

        NapCat 的伪造合并转发不识别 video 节点内的文件——即使已通过 stream
        上传到 NapCat 本机可访问路径，发送仍报“识别URL失败”。因此视频必须
        拆出合并转发，作为独立单条视频消息发送。

        发送策略（每个视频）：
        1. 本地文件：优先经 NapCat stream 上传到 NapCat 可访问路径后单发；
        2. 上传不可用/单发失败：尝试直发本地路径（兼容共享文件系统部署）；
        3. 仍无法送达：补发一条可点击的原始视频链接文本，保证内容可达。

        Args:
            session_id: 会话 ID
            video_components: 摘出的视频组件列表
            bot_client: 支持 call_action 的 bot 客户端（可能为 None）

        Returns:
            发送失败记录列表（失败项已各自补发链接降级）。
        """
        from astrbot.api.message_components import Plain, Video

        failures: list[SendResult] = []

        def _http_url(value: str) -> str:
            value = value.strip()
            return value if value.startswith(("http://", "https://")) else ""

        for component in video_components:
            file_value = str(component.file or "").strip()
            original_url = _http_url(str(component.original_url or ""))
            local_path = self._extract_local_video_path(component)

            if local_path is not None:
                # 1) 有本地文件：先尝试 stream 上传到 NapCat，上传不可用时直发本地路径
                uploaded_path = None
                if bot_client is not None:
                    uploaded_path = await upload_file_stream(bot_client, local_path)
                target_file = uploaded_path or file_value
                result = await self._send_chain(
                    session_id,
                    [Video(file=target_file)],
                )
                if result.ok:
                    continue
                logger.warning(
                    "OneBot detached video send failed: session=%s, file=%s, "
                    "detail=%s",
                    session_id,
                    target_file,
                    result.detail,
                )
                self._merge_send_failure(failures, result, stage="send_detached_video")
            elif _http_url(file_value):
                # 2) 无本地文件但持有 URL（兜底场景）：直接补发可点击链接
                link_url = original_url or file_value
                link_result = await self._send_chain(
                    session_id,
                    [Plain(f"🎬 视频: {link_url}")],
                )
                if not link_result.ok:
                    self._merge_send_failure(
                        failures, link_result, stage="send_video_link"
                    )
                continue
            else:
                # 既非本地文件也非 URL：无可用发送载体
                continue

            # 3) 单发失败降级：补发可点击的原始视频链接
            if original_url:
                link_result = await self._send_chain(
                    session_id,
                    [Plain(f"🎬 视频（无法直接发送）: {original_url}")],
                )
                if not link_result.ok:
                    self._merge_send_failure(
                        failures, link_result, stage="send_video_link"
                    )

        return failures

    async def _stream_upload_nodes(
        self, bot_client: Any, nodes: list[Node]
    ) -> list[Node]:
        """通过 NapCat Stream 上传节点中的本地视频文件

        Args:
            bot_client: 支持 call_action 的 bot 客户端
            nodes: 原始节点列表

        Returns:
            处理后的节点列表（本地视频文件路径替换为上传后的路径）
        """
        from astrbot.api.message_components import Video

        streamed_nodes: list[Node] = []
        for node in nodes:
            if not node.content:
                streamed_nodes.append(node)
                continue

            streamed_content = []
            for comp in node.content:
                if not isinstance(comp, Video):
                    streamed_content.append(comp)
                    continue

                local_path = self._extract_local_video_path(comp)
                if not local_path:
                    streamed_content.append(comp)
                    continue

                uploaded_path = await upload_file_stream(bot_client, local_path)
                if uploaded_path:
                    logger.info(
                        "[napcat_stream] 视频上传成功: local=%s, remote=%s",
                        local_path,
                        uploaded_path,
                    )
                    streamed_content.append(Video(file=uploaded_path))
                else:
                    logger.warning(
                        "[napcat_stream] 视频上传失败，保留原路径: local=%s",
                        local_path,
                    )
                    streamed_content.append(comp)

            streamed_nodes.append(
                Node(content=streamed_content, name=node.name, uin=node.uin)
            )

        return streamed_nodes

    def _has_local_video_nodes(self, nodes: list[Node]) -> bool:
        """检查节点列表中是否包含本地视频文件"""
        from astrbot.api.message_components import Video

        for node in nodes:
            if not node.content:
                continue
            for comp in node.content:
                if isinstance(comp, Video) and self._extract_local_video_path(comp):
                    return True
        return False

    @staticmethod
    def _extract_local_video_path(video_comp) -> Path | None:
        """从 Video 组件中提取本地文件路径

        Args:
            video_comp: Video 组件

        Returns:
            本地文件路径，如果不是本地文件则返回 None
        """
        file_value = getattr(video_comp, "file", None)
        if not isinstance(file_value, str) or not file_value:
            return None

        # 处理 file:/// 协议
        if file_value.startswith("file:///"):
            path = Path(file_value[8:])
        elif file_value.startswith("http://") or file_value.startswith("https://"):
            # 跳过 HTTP URL
            return None
        else:
            path = Path(file_value)

        return path if path.exists() and path.is_file() else None
