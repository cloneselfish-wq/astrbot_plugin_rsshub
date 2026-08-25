"""Entry content handler runtime for RSS push pipeline."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from pydantic import Field
from pydantic.dataclasses import dataclass as pydantic_dataclass

try:
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.agent.tool import FunctionTool, ToolExecResult, ToolSet
    from astrbot.core.astr_agent_context import AstrAgentContext
    from astrbot.core.message.message_event_result import MessageEventResult
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
except Exception:  # pragma: no cover - lightweight test fallback

    class ContextWrapper:  # type: ignore[no-redef]
        pass

    ToolExecResult = str  # type: ignore[assignment]

    @dataclass
    class FunctionTool:  # type: ignore[no-redef]
        name: str
        description: str
        parameters: dict

        @classmethod
        def __class_getitem__(cls, _item):
            return cls

    class ToolSet:  # type: ignore[no-redef]
        def __init__(self, tools: list[Any] | None = None) -> None:
            self.tools = tools or []

    class AstrAgentContext:  # type: ignore[no-redef]
        pass

    class MessageEventResult:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.chain: list[Any] = []

        def message(self, text: str) -> MessageEventResult:
            self.chain = [text]
            return self

    class AstrMessageEvent:  # type: ignore[no-redef]
        unified_msg_origin: str = ""


try:
    from astrbot.core.provider.provider import Provider
except Exception:  # pragma: no cover - lightweight test fallback

    class Provider:  # type: ignore[no-redef]
        pass


from ...domain.entities.content_types import LayoutFragment
from ...domain.entities.handlers import (
    HandlerSpec,
    is_handler_enabled,
    normalize_handlers,
)
from ...domain.entities.subscription import (
    HANDLERS_MODE_DISABLED,
    HANDLERS_MODE_OVERRIDE,
    Subscription,
)
from ...domain.entities.user import User
from ...infrastructure.config import ContentHandlerSettings
from ...infrastructure.utils import detect_media_hint, get_logger
from ...shared.constants import (
    AiFilterInputScope,
    AiTransformScope,
    HandlerTraceStatus,
    HandlerType,
)
from .html_parser import HTMLParser
from .pipeline_comment import AiCommentTrigger

logger = get_logger()


def _extract_json_object(payload: str) -> dict[str, Any] | None:
    """从 AI 输出中尽量提取一个 JSON 对象。

    容忍常见 LLM 输出噪音：markdown 代码块围栏（```json ... ```）、
    前后缀解释文字、被截断时取第一个 ``{`` 到最后一个 ``}`` 之间的内容。
    无法提取时返回 None（由调用方决定重试或放行）。
    """
    if not payload:
        return None
    text = str(payload).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return None


class _SyntheticHandlerEvent(AstrMessageEvent):
    """Minimal event adapter for tool_loop_agent in non-chat paths.

    继承 AstrMessageEvent 以通过 AstrAgentContext 的 pydantic isinstance 校验，
    同时提供所有必要方法的最小实现，使 tool_loop_agent 在无真实事件时可用。
    """

    def __init__(
        self,
        *,
        unified_msg_origin: str,
        platform_name: str,
        sender_id: str,
    ) -> None:
        # 不调用 AstrMessageEvent.__init__() —— 框架父类可能要求复杂参数。
        # 这里只注入 tool_loop_agent 实际使用的最小子集。
        self.unified_msg_origin = str(unified_msg_origin or "").strip()
        self.role = "member"
        self._platform_name = str(platform_name or "").strip()
        self._sender_id = str(sender_id or "").strip()
        self._result: MessageEventResult | None = None
        self._extras: dict[str, Any] = {}

    def get_result(self) -> MessageEventResult | None:
        return self._result

    def set_result(self, result: MessageEventResult | str) -> None:
        if isinstance(result, str):
            self._result = MessageEventResult().message(result)
            return
        self._result = result

    def get_extra(self, key: str | None = None, default=None) -> Any:
        if key is None:
            return self._extras
        return self._extras.get(key, default)

    def set_extra(self, key: str, value: Any) -> None:
        self._extras[key] = value

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_platform_name(self) -> str:
        return self._platform_name

    async def send(self, _message) -> None:
        return None


@pydantic_dataclass
class XmlValidationTool(FunctionTool[AstrAgentContext]):
    name: str = "rss_validate_xml_fragment"
    description: str = (
        "Validate one RSS/Atom item or entry XML fragment. "
        "Returns ok=false with precise parse/safety errors."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "raw_xml": {
                    "type": "string",
                    "description": "Single item/entry XML fragment to validate.",
                }
            },
            "required": ["raw_xml"],
        }
    )

    async def call(
        self,
        _context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        raw_xml = str(kwargs.get("raw_xml") or "").strip()
        from .agent_xml_push_service import _parse_xml_root, _validate_xml_input

        try:
            _validate_xml_input(raw_xml)
            _parse_xml_root(raw_xml)
        except Exception as exc:
            return json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "rules": [
                        "只返回单个 item 或 entry 级 XML 片段",
                        "不要包含 DOCTYPE 或 ENTITY",
                        "不要输出 Markdown、解释文本或代码块包裹",
                    ],
                },
                ensure_ascii=False,
            )
        return json.dumps({"ok": True}, ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class EntryContentContext:
    """Raw entry payload before formatting for one subscription."""

    title: str
    summary: str
    content: str
    link: str
    author: str
    feed_title: str
    feed_link: str
    raw_xml: str = ""
    media_urls: tuple[str, ...] = ()
    media_items: tuple[tuple[str, str], ...] = ()
    layout: tuple[LayoutFragment, ...] = ()


@dataclass(frozen=True, slots=True)
class HandlerProcessResult:
    """Content handler result plus runtime trace."""

    entry: EntryContentContext
    allow: bool = True
    reason: str = ""
    trace: tuple[dict[str, Any], ...] = ()
    commentary: str = ""  # ai_comment 直连模式生成的评论正文；空表示无评论
    comment_trigger: AiCommentTrigger | None = None  # ai_comment 管道模式触发载荷


class ContentHandlerRuntime:
    """Resolve and execute builtin entry handlers."""

    def __init__(
        self,
        context: Any | None = None,
        settings: ContentHandlerSettings | None = None,
    ):
        self._context = context
        self._settings = settings or ContentHandlerSettings()

    def resolve_handlers(
        self,
        *,
        subscription: Subscription,
        user: User | None,
    ) -> list[HandlerSpec]:
        mode = str(getattr(subscription, "handlers_mode", "") or "").strip().lower()
        if mode == HANDLERS_MODE_DISABLED:
            active = []
        elif mode == HANDLERS_MODE_OVERRIDE:
            active = subscription.get_handlers()
        else:
            # inherit / 历史空值：订阅自带 handler 优先，其次继承用户级。
            # 这样每个群(订阅)配了自己的过滤/改写条件后立即生效，
            # 未配置的订阅则继续回落到用户级 handlers。
            active = subscription.get_handlers()
            if not active:
                active = user.get_handlers() if user else []
        return normalize_handlers(active)

    async def process_entry(
        self,
        *,
        subscription: Subscription,
        user: User | None,
        entry: EntryContentContext,
        session_id: str | None = None,
        event: AstrMessageEvent | Any | None = None,
        target_session: str | None = None,
        platform_name: str | None = None,
        user_id: str | None = None,
    ) -> EntryContentContext:
        result = await self.process_entry_with_trace(
            subscription=subscription,
            user=user,
            entry=entry,
            session_id=session_id,
            event=event,
            target_session=target_session,
            platform_name=platform_name,
            user_id=user_id,
        )
        return result.entry

    async def process_entry_with_trace(
        self,
        *,
        subscription: Subscription,
        user: User | None,
        entry: EntryContentContext,
        session_id: str | None = None,
        event: AstrMessageEvent | Any | None = None,
        target_session: str | None = None,
        platform_name: str | None = None,
        user_id: str | None = None,
    ) -> HandlerProcessResult:
        result = entry
        trace: list[dict[str, Any]] = []
        commentary = ""
        comment_trigger: AiCommentTrigger | None = None
        resolved = self.resolve_handlers(subscription=subscription, user=user)
        # ai_comment 开启时：推送给订阅群的正文不套用全局人格转述，
        # 人格只保留在独立评论（bot 用自己的口吻说话）与 ai_filter。
        comment_active = any(
            is_handler_enabled(spec) and spec.name == "ai_comment"
            for spec in resolved
        )
        for spec in resolved:
            if not is_handler_enabled(spec):
                trace.append(
                    {
                        "id": spec.id,
                        "name": spec.name,
                        "status": HandlerTraceStatus.DISABLED.value,
                    }
                )
                continue
            if spec.type != HandlerType.BUILTIN.value:
                logger.debug("跳过 external handler: %s", spec.id)
                trace.append(
                    {
                        "id": spec.id,
                        "name": spec.name,
                        "status": HandlerTraceStatus.SKIPPED.value,
                        "reason": "external handler",
                    }
                )
                continue
            try:
                if spec.name == "ai_filter":
                    allowed, reason, model_id = await self._run_ai_filter(
                        result,
                        spec.config,
                        session_id=session_id,
                    )
                    filter_trace: dict[str, Any] = {
                        "id": spec.id,
                        "name": spec.name,
                        "status": HandlerTraceStatus.OK.value,
                        "allow": allowed,
                        "reason": reason,
                        "scope": str(
                            spec.config.get("input_scope")
                            or AiFilterInputScope.TEXT.value
                        ),
                    }
                    if model_id:
                        filter_trace["model_id"] = model_id
                    trace.append(filter_trace)
                    if not allowed:
                        return HandlerProcessResult(
                            entry=result,
                            allow=False,
                            reason=reason,
                            trace=tuple(trace),
                        )
                elif spec.name == "ai_transform":
                    transform_result = await self._run_ai_transform(
                        result,
                        spec.config,
                        session_id=session_id,
                        event=event,
                        target_session=target_session,
                        platform_name=platform_name,
                        user_id=user_id
                        or getattr(user, "id", "")
                        or subscription.user_id,
                        skip_persona=comment_active,
                    )
                    result = transform_result["entry"]
                    trace.append(
                        {
                            "id": spec.id,
                            "name": spec.name,
                            "status": HandlerTraceStatus.OK.value,
                            **transform_result["trace"],
                        }
                    )
                elif spec.name == "ai_comment":
                    # 评论针对改写前的原始条目生成（原文是怎样的就评论怎样的），
                    # 而不是改写后的消息；评论内容不受 ai_transform 影响。
                    if self._settings.ai_comment_pipeline:
                        # 管道模式（默认）：不调 LLM、不读图，只构造触发载荷；
                        # 评论正文由 AstrBot 消息管道在转发成功后生成并回复
                        # （见 notification_dispatcher._dispatch_pipeline_comment）。
                        comment_text = ""
                        comment_prompt = str(
                            (spec.config or {}).get("prompt") or ""
                        )
                        comment_trigger = AiCommentTrigger(
                            entry=entry,
                            prompt=comment_prompt,
                            with_media=self._normalize_with_media(
                                (spec.config or {}).get("with_media")
                            ),
                            config=dict(spec.config or {}),
                        )
                        comment_trace: dict[str, Any] = {
                            "mode": "pipeline",
                            "with_media": comment_trigger.with_media,
                            "prompt_present": bool(comment_prompt.strip()),
                        }
                    else:
                        # 直连模式（ai_comment_pipeline=false）：v2.5.0 行为，
                        # 插件直连 text_chat 生成正文 + 图片描述。
                        comment_text, comment_trace = await self._run_ai_comment(
                            entry,
                            spec.config,
                            session_id=session_id,
                        )
                    commentary = str(comment_text or "").strip()
                    trace.append(
                        {
                            "id": spec.id,
                            "name": spec.name,
                            "status": HandlerTraceStatus.OK.value,
                            **comment_trace,
                        }
                    )
                else:
                    logger.debug("未知内置 handler，已跳过: %s", spec.name)
                    trace.append(
                        {
                            "id": spec.id,
                            "name": spec.name,
                            "status": HandlerTraceStatus.SKIPPED.value,
                            "reason": "unknown builtin handler",
                        }
                    )
            except Exception as exc:
                logger.warning(
                    "handler 执行失败，已回退上一步结果: %s (%s)",
                    spec.id,
                    exc,
                )
                trace.append(
                    {
                        "id": spec.id,
                        "name": spec.name,
                        "status": HandlerTraceStatus.ERROR.value,
                        "reason": str(exc),
                    }
                )
        return HandlerProcessResult(
            entry=result,
            trace=tuple(trace),
            commentary=commentary,
            comment_trigger=comment_trigger,
        )

    async def _run_ai_transform(
        self,
        entry: EntryContentContext,
        config: dict[str, Any],
        *,
        session_id: str | None = None,
        event: AstrMessageEvent | Any | None = None,
        target_session: str | None = None,
        platform_name: str | None = None,
        user_id: str | None = None,
        skip_persona: bool = False,
    ) -> dict[str, Any]:
        prompt = str((config or {}).get("prompt") or "").strip()
        if not prompt or self._context is None:
            return {
                "entry": entry,
                "trace": {
                    "scope": str(
                        (config or {}).get("scope") or AiTransformScope.PLAINTEXT.value
                    ),
                    "steps_used": 0,
                    "fallback": True,
                    "fallback_reason": "missing prompt or context",
                },
            }

        providers = self._resolve_provider_chain(session_id=session_id)
        if not providers:
            logger.warning("ai_transform 跳过：当前没有可用的对话模型 provider")
            return {
                "entry": entry,
                "trace": {
                    "scope": str(
                        (config or {}).get("scope") or AiTransformScope.PLAINTEXT.value
                    ),
                    "steps_used": 0,
                    "fallback": True,
                    "fallback_reason": "provider unavailable",
                },
            }

        transform_scope = self._normalize_transform_scope((config or {}).get("scope"))
        agent_event = self._resolve_agent_event(
            event=event,
            session_id=session_id,
            target_session=target_session,
            platform_name=platform_name,
            user_id=user_id,
        )
        provider_id = self._resolve_chat_provider_id(
            provider=providers[0],
            session_id=session_id,
        )
        if not provider_id:
            raise ValueError("ai_transform 无法解析当前对话 provider_id")

        if transform_scope == AiTransformScope.XML.value:
            return await self._run_ai_transform_xml(
                entry=entry,
                prompt=prompt,
                provider_id=provider_id,
                event=agent_event,
                skip_persona=skip_persona,
            )
        return await self._run_ai_transform_plaintext(
            entry=entry,
            prompt=prompt,
            providers=providers,
            session_id=session_id,
            skip_persona=skip_persona,
        )

    async def _run_ai_transform_plaintext(
        self,
        *,
        entry: EntryContentContext,
        prompt: str,
        providers: list[Provider],
        session_id: str | None = None,
        skip_persona: bool = False,
    ) -> dict[str, Any]:
        source_payload = {
            "title": entry.title,
            "summary": entry.summary,
            "content": entry.content,
            "link": entry.link,
            "author": entry.author,
            "feed_title": entry.feed_title,
            "feed_link": entry.feed_link,
            "media_urls": list(entry.media_urls),
        }
        request_prompt = (
            "你是 RSS 内容改写 agent。请根据用户要求改写 RSS 条目的文本字段。"
            "只返回 JSON 对象，不要输出解释、Markdown 或代码块。"
            '\n只允许返回这些字段中的任意子集：{"title":"...","summary":"...","content":"..."}'
            "\n缺省字段表示不改动；空字符串也视为不改动。"
            f"\n用户要求:\n{prompt}"
            f"\n\n条目数据:\n{json.dumps(source_payload, ensure_ascii=False)}"
        )
        system_prompt = "" if skip_persona else self._resolve_system_prompt()
        payload, model_id = await self._chat_with_backoff(
            providers=providers,
            prompt=request_prompt,
            session_id=session_id,
            system_prompt=system_prompt,
            label="ai_transform",
        )
        parsed = self._parse_transform_json(
            payload, required_fields={"title", "summary", "content"}
        )
        title = str(parsed.get("title") or entry.title).strip()
        summary = str(parsed.get("summary") or entry.summary).strip()
        content = str(parsed.get("content") or entry.content).strip()
        transform_trace: dict[str, Any] = {
            "scope": AiTransformScope.PLAINTEXT.value,
            "steps_used": 1,
            "fallback": False,
            "persona_applied": bool(system_prompt),
        }
        if model_id:
            transform_trace["model_id"] = model_id
        return {
            "entry": replace(
                entry,
                title=title or entry.title,
                summary=summary or entry.summary,
                content=content or entry.content,
            ),
            "trace": transform_trace,
        }

    async def _run_ai_transform_xml(
        self,
        *,
        entry: EntryContentContext,
        prompt: str,
        provider_id: str,
        event: AstrMessageEvent | Any,
        skip_persona: bool = False,
    ) -> dict[str, Any]:
        source_payload = {
            "raw_xml": entry.raw_xml,
            "title": entry.title,
            "link": entry.link,
            "author": entry.author,
            "feed_title": entry.feed_title,
            "feed_link": entry.feed_link,
        }
        persona_prompt = "" if skip_persona else self._resolve_system_prompt()
        system_prompt = "\n\n".join(
            part
            for part in [
                persona_prompt,
                (
                    "你是 RSS XML 改写 agent。你必须遵守 RSS/Atom item 或 entry 片段规范。"
                    "只返回 JSON 对象，且必须包含 raw_xml 字段。"
                    "raw_xml 只能是单个 item/entry 级 XML 片段，不允许 DOCTYPE/ENTITY，"
                    "不要输出解释文本、Markdown 或代码块包裹。"
                ),
            ]
            if part
        )
        request_prompt = (
            "请按用户要求改写下面的 RSS item/entry XML。必要时先调用 XML 校验工具自检并修正。"
            '\n最终只返回 JSON，例如 {"raw_xml":"<item>...</item>"}。'
            f"\n用户要求:\n{prompt}"
            f"\n\n条目数据:\n{json.dumps(source_payload, ensure_ascii=False)}"
        )
        response = await self._context.tool_loop_agent(
            event=event,
            chat_provider_id=provider_id,
            prompt=request_prompt,
            tools=ToolSet([XmlValidationTool()]),
            contexts=[],
            system_prompt=system_prompt,
            max_steps=6,
            tool_call_timeout=60,
            stream=False,
        )
        payload = str(getattr(response, "completion_text", "") or "").strip()
        parsed = self._parse_transform_json(payload, required_fields={"raw_xml"})
        transformed_xml = str(parsed.get("raw_xml") or "").strip()
        if not transformed_xml:
            raise ValueError("ai_transform(xml) 输出缺少 raw_xml")

        reparsed_entry = await self._reparse_transformed_xml(
            entry=entry, raw_xml=transformed_xml
        )
        steps_used = max(
            len(getattr(response, "tools_call_name", []) or []) + 1,
            1,
        )
        transform_trace: dict[str, Any] = {
            "scope": AiTransformScope.XML.value,
            "steps_used": steps_used,
            "fallback": False,
            "persona_applied": bool(persona_prompt),
        }
        if provider_id:
            transform_trace["model_id"] = provider_id
        return {
            "entry": reparsed_entry,
            "trace": transform_trace,
        }

    async def _run_ai_filter(
        self,
        entry: EntryContentContext,
        config: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> tuple[bool, str, str]:
        prompt = str((config or {}).get("prompt") or "").strip()
        if not prompt or self._context is None:
            return True, "ai_filter 未配置 prompt 或 provider 上下文", ""

        providers = self._resolve_provider_chain(session_id=session_id)
        if not providers:
            logger.warning("ai_filter 放行：当前没有可用的对话模型 provider")
            return True, "provider unavailable", ""

        input_scope = self._normalize_filter_scope((config or {}).get("input_scope"))
        source_payload = {
            "title": entry.title,
            "summary": entry.summary,
            "content": entry.content,
            "link": entry.link,
            "author": entry.author,
            "feed_title": entry.feed_title,
            "feed_link": entry.feed_link,
        }
        if input_scope in {
            AiFilterInputScope.RAW_XML.value,
            AiFilterInputScope.BOTH.value,
        }:
            source_payload["raw_xml"] = entry.raw_xml
        if input_scope == AiFilterInputScope.RAW_XML.value:
            source_payload = {
                "title": entry.title,
                "link": entry.link,
                "feed_title": entry.feed_title,
                "raw_xml": entry.raw_xml,
            }

        request_prompt = (
            "你是 RSS 内容过滤器。根据用户要求判断条目是否允许推送。"
            "只返回一个 JSON 对象，不要输出解释、Markdown 或代码块。"
            '\n返回格式: {"allow":true,"reason":"..."}'
            "\nallow=false 表示跳过推送；reason 用一句话说明原因。"
            f"\n用户要求:\n{prompt}"
            f"\n\n条目数据:\n{json.dumps(source_payload, ensure_ascii=False)}"
        )
        parsed, failure_reason, provider_id = await self._call_filter_once(
            providers=providers,
            request_prompt=request_prompt,
            session_id=session_id,
        )
        if parsed is None:
            logger.warning(
                "ai_filter 放行：%s（重试一次后仍失败，按放行处理）",
                failure_reason,
            )
            return True, failure_reason, ""
        if not isinstance(parsed, dict) or not isinstance(parsed.get("allow"), bool):
            logger.warning("ai_filter 放行：AI 返回结构无效")
            return True, "invalid schema", provider_id
        reason = str(parsed.get("reason") or "").strip()
        try:
            reason_max_length = int((config or {}).get("reason_max_length") or 120)
        except (TypeError, ValueError):
            reason_max_length = 120
        if reason_max_length > 0 and len(reason) > reason_max_length:
            reason = reason[:reason_max_length].rstrip()
        return bool(parsed["allow"]), reason, provider_id

    async def _run_ai_comment(
        self,
        entry: EntryContentContext,
        config: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """生成一条 AI 评论（吐槽/观点/看法），失败时 fail-open 返回空串。

        评论基于**改写前的原始条目**（调用方传入的 entry 应为 handler 链输入，
        而非 ai_transform 之后的 result）。图片描述单独走图片描述 provider
        （无退避，单图失败跳过），评论正文走 ``_chat_with_backoff``
        （指数退避 + 回退 provider 链）。返回 ``(评论正文, trace_dict)``。
        """
        prompt = str((config or {}).get("prompt") or "").strip()
        if not prompt or self._context is None:
            return "", {
                "fallback": True,
                "fallback_reason": "missing prompt or context",
            }

        providers = self._resolve_provider_chain(session_id=session_id)
        if not providers:
            logger.warning("ai_comment 跳过：当前没有可用的对话模型 provider")
            return "", {
                "fallback": True,
                "fallback_reason": "provider unavailable",
            }

        source_payload: dict[str, Any] = {
            "title": entry.title,
            "summary": entry.summary,
            "content": entry.content,
            "link": entry.link,
            "author": entry.author,
            "feed_title": entry.feed_title,
            "feed_link": entry.feed_link,
            "media_urls": list(entry.media_urls),
        }

        images_read = 0
        with_media = self._normalize_with_media((config or {}).get("with_media"))
        if with_media:
            image_captions = await self._describe_comment_images(entry)
            images_read = len(image_captions)
            if image_captions:
                source_payload["image_captions"] = image_captions

        request_prompt = (
            "你是 RSS 推送评论 agent。请像人一样和订阅群里的人们一起看这条推送，"
            "写一段吐槽/观点/看法的评论。"
            "只输出评论正文本身，不要解释、不要 Markdown、不要 JSON 包裹、不要引用字段名。"
            f"\n用户要求:\n{prompt}"
            f"\n\n条目数据:\n{json.dumps(source_payload, ensure_ascii=False)}"
        )
        commentary, model_id = await self._chat_with_backoff(
            providers=providers,
            prompt=request_prompt,
            session_id=session_id,
            system_prompt=self._resolve_system_prompt(),
            label="ai_comment",
        )
        commentary = str(commentary or "").strip()
        # 主 provider 限流/失败后评论会由回退 provider 生成（v2.6.0 首次推送
        # 日志中 Anthropic 连续限流 5 次后切到 deepseek-v4-flash 即此情况），
        # 口吻表现与视觉能力可能劣化——显式记录该事实，便于察觉质量劣化。
        primary_id = self._provider_identity(providers[0])
        provider_fallback = bool(model_id and primary_id and model_id != primary_id)
        if provider_fallback:
            logger.warning(
                "ai_comment 评论由回退 provider 生成（%s，主 provider %s 限流/失败），"
                "口吻与图片描述质量可能劣化",
                model_id,
                primary_id,
            )
        comment_trace: dict[str, Any] = {
            "with_media": bool(with_media),
            "images_read": images_read,
            "commentary_present": bool(commentary),
            "commentary_length": len(commentary),
            "fallback": False,
            "provider_fallback": provider_fallback,
        }
        if model_id:
            comment_trace["model_id"] = model_id
        return commentary, comment_trace

    async def generate_comment_text(
        self,
        entry: EntryContentContext,
        config: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """直连模式生成评论正文（公开 API）。

        等价于 ``_run_ai_comment``：基于改写前的原始条目、可读图、指数退避 +
        回退 provider 链，失败 fail-open 返回 ``("", trace)``。供 dispatcher
        在管道注入失败时做直连回退（与 ``ai_comment_pipeline`` 开关无关）。
        """
        return await self._run_ai_comment(
            entry,
            config,
            session_id=session_id,
        )

    async def _describe_comment_images(self, entry: EntryContentContext) -> list[str]:
        """对条目图片逐张调用图片描述 provider，返回 ``["<url>: <desc>"]`` 列表。

        单图失败只 warning 跳过，不阻断评论生成（评论是增强层）。
        """
        image_urls = self._collect_comment_image_urls(entry)
        if not image_urls:
            return []
        caption_provider = self._resolve_image_caption_provider()
        if caption_provider is None:
            logger.warning("ai_comment 未解析到图片描述 provider，评论将只看文字")
            return []
        caption_prompt = self._resolve_image_caption_prompt()
        captions: list[str] = []
        for image_url in image_urls:
            try:
                desc = await self._call_image_caption_once(
                    provider=caption_provider,
                    prompt=caption_prompt,
                    image_url=image_url,
                )
            except Exception as exc:
                logger.warning(
                    "ai_comment 图片描述失败，跳过该图: %s (%s)",
                    image_url,
                    exc,
                )
                continue
            if desc:
                captions.append(f"{image_url}: {desc}")
        return captions

    def _collect_comment_image_urls(
        self,
        entry: EntryContentContext,
        *,
        cap: int = 3,
    ) -> list[str]:
        """收集条目中的图片 URL（media_items 显式 image + media_urls 推断 image）。"""
        collected: list[str] = []
        seen: set[str] = set()

        def append(url: str) -> None:
            url = str(url or "").strip()
            if url and url not in seen:
                seen.add(url)
                collected.append(url)

        for media_type, media_url in entry.media_items:
            if str(media_type or "").strip().lower() == "image":
                append(media_url)
        for media_url in entry.media_urls:
            try:
                detection = detect_media_hint(url=str(media_url or ""))
            except Exception:
                detection = None
            if detection is not None and detection.media_type == "image":
                append(media_url)
        return collected[:cap]

    def _resolve_image_caption_provider(
        self,
        *,
        session_id: str | None = None,
    ) -> Provider | None:
        """按优先级解析图片描述 provider：

        插件 ``ai_comment_image_provider_id`` → AstrBot ``default_image_caption_provider_id``
        → ``provider_ltm_settings.image_caption_provider_id`` → 当前对话 provider。
        """
        candidate_ids: list[str] = []
        configured_id = str(
            self._settings.ai_comment_image_provider_id or ""
        ).strip()
        if configured_id:
            candidate_ids.append(configured_id)
        candidate_ids.extend(self._read_astrbot_image_caption_provider_id())
        for provider_id in candidate_ids:
            provider = self._resolve_provider_by_id(provider_id)
            if provider is not None:
                return provider
        return self._resolve_provider(session_id=session_id)

    def _read_astrbot_image_caption_provider_id(self) -> list[str]:
        """读取 AstrBot 配置里的图片描述 provider id（两级候选，按序回退）。"""
        config = self._read_astrbot_config()
        if not config:
            return []
        provider_settings = config.get("provider_settings") or {}
        provider_ltm_settings = config.get("provider_ltm_settings") or {}
        default_id = str(
            (provider_settings.get("default_image_caption_provider_id") or "").strip()
        )
        ltm_id = str(
            (provider_ltm_settings.get("image_caption_provider_id") or "").strip()
        )
        return [item for item in (default_id, ltm_id) if item]

    def _read_astrbot_config(self) -> dict[str, Any]:
        """防御性读取 AstrBot 全局配置；取不到返回空 dict。"""
        if self._context is None:
            return {}
        getter = getattr(self._context, "get_config", None)
        if getter is not None:
            try:
                config = getter()
                if isinstance(config, dict):
                    return config
                data = getattr(config, "data", None)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        config_mgr = getattr(self._context, "astrbot_config_mgr", None)
        if config_mgr is not None:
            try:
                confs = getattr(config_mgr, "confs", None) or {}
                default_conf = confs.get("default")
                if isinstance(default_conf, dict):
                    return default_conf
            except Exception:
                pass
        return {}

    def _resolve_image_caption_prompt(self) -> str:
        """读取 AstrBot 图片描述提示词，空则使用默认中文提示词。"""
        config = self._read_astrbot_config()
        provider_settings = config.get("provider_settings") or {}
        prompt = str(
            (provider_settings.get("image_caption_prompt") or "").strip()
        )
        return prompt or "请用一句话描述这张图片的内容。"

    async def _call_image_caption_once(
        self,
        *,
        provider: Provider,
        prompt: str,
        image_url: str,
    ) -> str:
        """对单张图片调用一次图片描述；每次独立 session，避免污染会话上下文。"""
        response = await provider.text_chat(
            prompt=prompt,
            session_id=uuid4().hex,
            contexts=[],
            persist=False,
            image_urls=[image_url],
        )
        return str(getattr(response, "completion_text", "") or "").strip()

    def _normalize_with_media(self, value: Any) -> bool:
        """防御性解析 with_media 布尔值（null/空串回退默认 True）。"""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or "").strip().lower()
        if text in {"0", "false", "no", "off", "禁用", "否"}:
            return False
        return True

    async def _chat_with_backoff(
        self,
        *,
        providers: list[Any],
        prompt: str,
        session_id: str | None,
        system_prompt: str | None = None,
        max_attempts: int = 3,
        base_delay: float = 1.5,
        label: str = "provider",
    ) -> tuple[str, str]:
        """按 provider 顺序调用 text_chat：先对瞬时异常指数退避重试，
        单 provider 连续失败后切换到下一个回退 provider。

        Args:
            providers: 有序候选链（[主, *回退]），按顺序尝试。
            max_attempts: 每个 provider 的重试次数。
            base_delay: 首次退避延迟，之后 2 倍递增。

        Returns:
            (completion_text, provider_id) 元组；provider_id 是最终成功调用的
            provider 身份（可能为空串）。全部 provider 失败后抛出最后一次异常，
            由外层 per-handler except 记录 error trace 并 fail-open，语义不变。
        """
        last_exc: Exception | None = None
        for provider in providers:
            for attempt in range(1, max_attempts + 1):
                try:
                    response = await provider.text_chat(
                        prompt=prompt,
                        session_id=session_id or "rsshub-handlers",
                        contexts=[],
                        persist=False,
                        system_prompt=system_prompt,
                    )
                    return (
                        str(getattr(response, "completion_text", "") or "").strip(),
                        self._provider_identity(provider),
                    )
                except Exception as exc:
                    last_exc = exc
                    if attempt >= max_attempts:
                        if len(providers) > 1:
                            logger.warning(
                                "%s 在 %s 连续失败，切换下一个回退 provider：%s",
                                label,
                                self._provider_identity(provider) or "provider",
                                exc,
                            )
                        break
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "%s 调用失败（第 %s/%s 次）：%s；%.1fs 后重试",
                        label,
                        attempt,
                        max_attempts,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def _call_filter_once(
        self,
        *,
        providers: list[Any],
        request_prompt: str,
        session_id: str | None,
    ) -> tuple[dict[str, Any] | None, str, str]:
        """调用一次过滤判定，容忍非 JSON 输出并重试一次。

        Args:
            providers: 有序候选链（[主, *回退]），失败时按顺序切换。

        Returns:
            (解析后的 JSON 对象, 失败原因, provider_id)；成功时失败原因为空字符串，
            provider_id 为最终成功调用的 provider 身份；失败放行时 provider_id 为空串。
        """
        payload, provider_id = await self._chat_with_backoff(
            providers=providers,
            prompt=request_prompt,
            session_id=session_id,
            system_prompt=self._resolve_system_prompt(),
            label="ai_filter",
        )
        if not payload:
            logger.warning("ai_filter 放行：AI 返回为空")
            return None, "empty response", ""
        parsed = _extract_json_object(payload)
        if parsed is not None:
            return parsed, "", provider_id
        # 重试一次：明确要求只返回 JSON
        logger.warning("ai_filter 解析失败，重试一次：AI 返回非 JSON 输出")
        retry_prompt = (
            request_prompt
            + "\n\n你上一次的输出不是合法 JSON。请只返回一个 JSON 对象，"
            '格式为 {"allow":true,"reason":"..."}，不要包含 Markdown 代码块、'
            "解释文字或任何其他内容。"
        )
        retry_payload, retry_provider_id = await self._chat_with_backoff(
            providers=providers,
            prompt=retry_prompt,
            session_id=session_id,
            system_prompt=self._resolve_system_prompt(),
            label="ai_filter",
        )
        if not retry_payload:
            return None, "empty response", ""
        retry_parsed = _extract_json_object(retry_payload)
        if retry_parsed is not None:
            return retry_parsed, "", retry_provider_id
        return None, "invalid json", ""

    def _normalize_filter_scope(self, value: Any) -> str:
        normalized = str(value or "").strip()
        if normalized not in {item.value for item in AiFilterInputScope}:
            return AiFilterInputScope.TEXT.value
        return normalized

    def _normalize_transform_scope(self, value: Any) -> str:
        normalized = str(value or "").strip()
        if normalized not in {item.value for item in AiTransformScope}:
            return AiTransformScope.PLAINTEXT.value
        return normalized

    def _parse_transform_json(
        self,
        payload: str,
        *,
        required_fields: set[str],
    ) -> dict[str, Any]:
        if not payload:
            raise ValueError("ai_transform 输出为空")
        parsed = _extract_json_object(payload)
        if parsed is None:
            raise ValueError("ai_transform 输出不是合法 JSON（含容忍解析）")
        if not isinstance(parsed, dict):
            raise ValueError("ai_transform 输出必须是 JSON 对象")
        allowed_fields = {"title", "summary", "content", "raw_xml"}
        invalid_keys = [key for key in parsed.keys() if key not in allowed_fields]
        if invalid_keys:
            raise ValueError(
                f"ai_transform 输出包含非法字段: {', '.join(invalid_keys)}"
            )
        if required_fields and not any(
            str(parsed.get(field) or "").strip() for field in required_fields
        ):
            raise ValueError("ai_transform 输出缺少有效结果")
        return parsed

    async def _reparse_transformed_xml(
        self,
        *,
        entry: EntryContentContext,
        raw_xml: str,
    ) -> EntryContentContext:
        from .agent_xml_push_service import (
            _collect_xml_media,
            _parse_xml_root,
            _validate_xml_input,
        )

        normalized_xml = _validate_xml_input(raw_xml)
        root, body_xml = _parse_xml_root(normalized_xml)
        parsed = await HTMLParser(
            body_xml, feed_link=entry.feed_link or entry.link or ""
        ).parse()
        from .feed_polling_service import FeedPollingService

        plain_body = FeedPollingService._remove_media_placeholders(
            parsed.html_tree.get_plain().strip()
        )
        content = await FeedPollingService._format_dispatch_content_async(
            title=str(root.findtext("title") or entry.title or "").strip(),
            body=plain_body,
            link=str(root.findtext("link") or entry.link or "").strip(),
            feed_title=entry.feed_title,
            feed_link=entry.feed_link,
            author=str(root.findtext("author") or entry.author or "").strip(),
        )
        media_items = FeedPollingService._media_items_from_parsed(parsed.media)
        media_urls = [url for _media_type, url in media_items]
        media_urls.extend(_collect_xml_media(root))
        deduped_media_urls = tuple(dict.fromkeys(media_urls))
        return replace(
            entry,
            title=str(root.findtext("title") or entry.title or "").strip()
            or entry.title,
            summary=plain_body or entry.summary,
            content=content or entry.content,
            link=str(root.findtext("link") or entry.link or "").strip() or entry.link,
            author=str(root.findtext("author") or entry.author or "").strip()
            or entry.author,
            raw_xml=normalized_xml,
            media_urls=deduped_media_urls,
            media_items=tuple(media_items),
            layout=tuple(parsed.layout),
        )

    def _resolve_agent_event(
        self,
        *,
        event: AstrMessageEvent | Any | None,
        session_id: str | None,
        target_session: str | None,
        platform_name: str | None,
        user_id: str | None,
    ) -> AstrMessageEvent | Any:
        if event is not None:
            return event
        resolved_target = (
            str(target_session or "").strip()
            or str(session_id or "").strip()
            or "rsshub:FriendMessage:rsshub-handlers"
        )
        resolved_platform = (
            str(platform_name or "").strip()
            or self._platform_name_from_session(resolved_target)
            or "rsshub"
        )
        return _SyntheticHandlerEvent(
            unified_msg_origin=resolved_target,
            platform_name=resolved_platform,
            sender_id=str(user_id or "rsshub-handler"),
        )

    def _platform_name_from_session(self, target_session: str) -> str:
        parts = str(target_session or "").split(":", 2)
        return parts[0].strip() if parts else ""

    def _resolve_chat_provider_id(
        self,
        *,
        provider: Provider,
        session_id: str | None,
    ) -> str:
        configured_id = self._settings.ai_provider_id.strip()
        if configured_id:
            identity = self._provider_identity(provider)
            # 传进来的就是配置的主 provider → 用配置 id；
            # 否则（如主 id 不可解析时退到回退 provider）返回它自己的 id，
            # 避免把不可解析的主 id 喂给 tool_loop_agent。
            if not identity or identity == configured_id:
                return configured_id
            return identity
        meta = getattr(provider, "meta", None)
        if callable(meta):
            try:
                return str(meta().id or "").strip()
            except Exception:
                return ""
        return ""

    def _provider_identity(self, provider: Provider) -> str:
        """返回 provider 的稳定标识，用于回退链去重；取不到时返回空串。"""
        meta = getattr(provider, "meta", None)
        if callable(meta):
            try:
                provider_id = str(meta().id or "").strip()
                if provider_id:
                    return provider_id
            except Exception:
                pass
        provider_config = getattr(provider, "provider_config", None)
        if isinstance(provider_config, dict):
            provider_id = str(provider_config.get("id") or "").strip()
            if provider_id:
                return provider_id
        raw_id = getattr(provider, "id", None)
        if raw_id:
            return str(raw_id).strip()
        return ""

    def _resolve_provider_by_id(self, provider_id: str) -> Provider | None:
        """按 id 解析 provider，校验 text_chat 可调用；失败返回 None。"""
        getter_by_id = getattr(self._context, "get_provider_by_id", None)
        if getter_by_id is not None:
            try:
                provider = getter_by_id(provider_id)
                if callable(getattr(provider, "text_chat", None)):
                    return provider
            except Exception:
                pass
        provider_manager = getattr(self._context, "provider_manager", None)
        getter = getattr(provider_manager, "get_provider_by_id", None)
        if getter is not None:
            try:
                provider = getter(provider_id)
                if callable(getattr(provider, "text_chat", None)):
                    return provider
            except Exception:
                pass
        return None

    def _resolve_provider_chain(self, *, session_id: str | None = None) -> list[Provider]:
        """按配置顺序返回 [主 provider, *回退 provider]，去重、跳过不可解析项。

        - 主 provider 解析失败（如配置的 ai_provider_id 不可用）时仍保留回退项，
          避免单点配置错误导致 filter/transform 整体不可用。
        - 同一 provider 只保留一次（按 _provider_identity 去重；身份为空不去重，
          保证不同对象不被折叠成一个）。
        """
        chain: list[Provider] = []
        seen: set[str] = set()

        primary = self._resolve_provider(session_id=session_id)
        fallback_providers: list[Provider] = []
        for fallback_id in self._settings.ai_fallback_providers:
            fallback_id = str(fallback_id or "").strip()
            if not fallback_id:
                continue
            fallback = self._resolve_provider_by_id(fallback_id)
            if fallback is None:
                logger.warning("ai 回退 provider %s 无法解析，已跳过", fallback_id)
                continue
            fallback_providers.append(fallback)

        for provider in (
            [primary] if primary is not None else []
        ) + fallback_providers:
            identity = self._provider_identity(provider)
            if identity:
                if identity in seen:
                    continue
                seen.add(identity)
            chain.append(provider)
        return chain

    def _resolve_provider(self, *, session_id: str | None = None) -> Provider | None:
        provider = None
        provider_id = self._settings.ai_provider_id.strip()
        if provider_id:
            provider = self._resolve_provider_by_id(provider_id)
        if provider is None:
            getter = getattr(self._context, "get_using_provider", None)
            if getter is None:
                return None
            # 先尝试按目标会话查找当前活跃的 provider（命令响应场景），
            # 若会话无活跃 provider（自动推送 / Web 测试推送场景），
            # 则回退到全局默认 provider。
            if session_id:
                provider = getter(session_id)
            if provider is None:
                provider = getter()
        return provider if callable(getattr(provider, "text_chat", None)) else None

    def _resolve_system_prompt(self) -> str:
        persona_id = self._settings.ai_persona_id.strip()
        if not persona_id:
            return ""
        persona_manager = getattr(self._context, "persona_manager", None)
        getter = getattr(persona_manager, "get_persona_v3_by_id", None)
        if getter is None:
            logger.warning("内容处理器人格未生效：persona_manager 不可用")
            return ""
        persona = getter(persona_id)
        if not persona:
            logger.warning("内容处理器人格未生效：找不到 persona_id=%s", persona_id)
            return ""
        prompt = getattr(persona, "system_prompt", None)
        if prompt is None and isinstance(persona, dict):
            prompt = persona.get("prompt") or persona.get("system_prompt")
        return str(prompt or "").strip()
