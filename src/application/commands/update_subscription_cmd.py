"""
更新订阅选项命令

处理更新订阅配置选项的业务用例。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...domain.entities.handlers import (
    DEFAULT_AI_COMMENT_PROMPT,
    DEFAULT_MERGE_MAX_CHARS,
    DEFAULT_MERGE_MAX_IMAGES,
    HANDLER_STATUS_ENABLED,
    build_ai_comment_handler,
    build_merge_condition_handler,
    parse_handlers_input,
)
from ...domain.entities.subscription import SUPPORTED_HANDLERS_MODES
from ...domain.repositories.subscription_repository import SubscriptionRepository
from ...infrastructure.config import validate_interval_value
from ..dto.result_dto import CommandResult
from ..dto.subscription_dto import SubscriptionDTO

if TYPE_CHECKING:
    from .get_user_settings_cmd import GetUserSettingsCommand

REMOVED_OPTIONS = {
    "translate",
    "translate_target_lang",
    "use_sub_config",
    "ai_prompt",
}
STRING_OPTIONS = {
    "title",
    "tags",
    "target_session",
    "platform_name",
    "handlers_mode",
}
JSON_OPTIONS = {"handlers"}

COMMENT_HANDLER_NAME = "ai_comment"
MERGE_CONDITION_HANDLER_NAME = "merge_condition"


def _coerce_optional_int(value: Any) -> int | None:
    """把可选阈值强转为 int；None / 非法值返回 None（表示不改动）。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _reconcile_ai_comment_handlers(
    base_handlers: list[dict[str, Any]],
    *,
    enabled: bool,
    prompt: str | None,
    user_handlers: list[dict[str, Any]],
    mode: str,
) -> list[dict[str, Any]]:
    """把 ai_comment 开关落进订阅 handlers 链（输入输出都是归一化 dict 列表）。

    - 启用：若订阅链去掉 ai_comment 后为空且处于 ``inherit`` 模式，先快照合并
      用户全局 handlers，避免 inherit 全有或全无语义下把用户全局改写需求静默
      清空成只有一条评论；随后 upsert 一个启用的 ai_comment（保留已有 id /
      config，缺 prompt 时补默认口吻）。
    - 关闭：从订阅链移除 ai_comment；链可能因此重新为空并在 inherit 下回落到
      用户全局链。
    """
    base = [dict(h) for h in base_handlers or []]

    if not enabled:
        return [
            h
            for h in base
            if str(h.get("name", "")).strip() != COMMENT_HANDLER_NAME
        ]

    others = [
        h
        for h in base
        if str(h.get("name", "")).strip() != COMMENT_HANDLER_NAME
    ]
    if mode == "inherit" and not others:
        base = [dict(h) for h in user_handlers or []]

    existing_idx = next(
        (
            i
            for i, h in enumerate(base)
            if str(h.get("name", "")).strip() == COMMENT_HANDLER_NAME
        ),
        None,
    )
    if existing_idx is not None:
        existing = base[existing_idx]
        config = dict(existing.get("config") or {})
        if prompt:
            config["prompt"] = prompt
        config.setdefault("prompt", DEFAULT_AI_COMMENT_PROMPT)
        config.setdefault("with_media", True)
        base[existing_idx] = {
            "id": str(existing.get("id") or "").strip()
            or "builtin.ai_comment.default",
            "type": "builtin",
            "name": COMMENT_HANDLER_NAME,
            "status": HANDLER_STATUS_ENABLED,
            "config": config,
        }
        return base

    new_handlers = build_ai_comment_handler(prompt or DEFAULT_AI_COMMENT_PROMPT)
    if not new_handlers:
        return base
    base.extend(new_handlers)
    return base


def _reconcile_merge_condition_handlers(
    base_handlers: list[dict[str, Any]],
    *,
    enabled: bool,
    max_chars: int | None,
    max_images: int | None,
    user_handlers: list[dict[str, Any]],
    mode: str,
) -> list[dict[str, Any]]:
    """把「合并转发条件」开关落进订阅 handlers 链。

    语义与 ai_comment 开关一致：
    - 启用：inherit 且订阅链去掉 merge_condition 后为空时，先快照用户全局
      handlers，避免清空全局改写需求；随后 upsert 一个启用的 merge_condition。
    - 关闭：从订阅链移除 merge_condition。
    """
    base = [dict(h) for h in base_handlers or []]

    if not enabled:
        return [
            h
            for h in base
            if str(h.get("name", "")).strip() != MERGE_CONDITION_HANDLER_NAME
        ]

    others = [
        h
        for h in base
        if str(h.get("name", "")).strip() != MERGE_CONDITION_HANDLER_NAME
    ]
    if mode == "inherit" and not others:
        base = [dict(h) for h in user_handlers or []]

    existing_idx = next(
        (
            i
            for i, h in enumerate(base)
            if str(h.get("name", "")).strip() == MERGE_CONDITION_HANDLER_NAME
        ),
        None,
    )
    if existing_idx is not None:
        existing = base[existing_idx]
        config = dict(existing.get("config") or {})
        if max_chars is not None:
            config["max_chars"] = max_chars
        if max_images is not None:
            config["max_images"] = max_images
        config.setdefault("max_chars", DEFAULT_MERGE_MAX_CHARS)
        config.setdefault("max_images", DEFAULT_MERGE_MAX_IMAGES)
        base[existing_idx] = {
            "id": str(existing.get("id") or "").strip()
            or "builtin.merge_condition.default",
            "type": "builtin",
            "name": MERGE_CONDITION_HANDLER_NAME,
            "status": HANDLER_STATUS_ENABLED,
            "config": config,
        }
        return base

    base.extend(
        build_merge_condition_handler(
            max_chars if max_chars is not None else DEFAULT_MERGE_MAX_CHARS,
            max_images if max_images is not None else DEFAULT_MERGE_MAX_IMAGES,
        )
    )
    return base


class UpdateSubscriptionCommand:
    """
    更新订阅选项命令

    处理更新订阅配置选项的业务用例。
    """

    def __init__(
        self,
        subscription_repo: SubscriptionRepository,
        get_user_settings_cmd: GetUserSettingsCommand | None = None,
    ):
        self._subscription_repo = subscription_repo
        self._get_user_settings_cmd = get_user_settings_cmd

    async def execute(
        self,
        sub_id: int,
        user_id: str,
        **options,
    ) -> CommandResult:
        """
        执行更新命令

        Args:
            sub_id: 订阅 ID
            user_id: 用户 ID
            **options: 要更新的选项

        Returns:
            CommandResult: 命令执行结果
        """
        removed = sorted(REMOVED_OPTIONS.intersection(options))
        if removed:
            return CommandResult(
                success=False,
                message=("订阅翻译选项已移除: " + ", ".join(removed)),
            )
        # ai_comment / merge_condition 开关不直接落库，由命令在归一化后
        # reconcile 进 handlers。
        comment_spec = options.pop("ai_comment", None)
        merge_spec = options.pop("merge_condition", None)

        normalized_options = {}
        for key, value in options.items():
            if key in STRING_OPTIONS:
                normalized_value = str(value or "").strip()
                if key == "handlers_mode":
                    normalized_value = normalized_value.lower()
                    if normalized_value not in SUPPORTED_HANDLERS_MODES:
                        return CommandResult(
                            success=False,
                            message="handlers_mode 只支持 inherit / override / disabled",
                        )
                normalized_options[key] = normalized_value
                continue
            if key in JSON_OPTIONS:
                try:
                    normalized_options[key] = parse_handlers_input(value)
                except ValueError as exc:
                    return CommandResult(success=False, message=str(exc))
                continue
            if key == "interval":
                try:
                    normalized_options[key] = validate_interval_value(
                        value,
                        allow_inherit=True,
                        field_name="interval",
                    )
                except ValueError as exc:
                    return CommandResult(success=False, message=str(exc))
                continue
            normalized_options[key] = value

        if comment_spec is not None:
            error = await self._apply_ai_comment_option(
                normalized_options,
                comment_spec,
                sub_id,
                user_id,
            )
            if error:
                return CommandResult(success=False, message=error)

        if merge_spec is not None:
            error = await self._apply_merge_condition_option(
                normalized_options,
                merge_spec,
                sub_id,
                user_id,
            )
            if error:
                return CommandResult(success=False, message=error)

        subscription = await self._subscription_repo.update_options(
            sub_id, user_id, **normalized_options
        )
        if not subscription:
            return CommandResult(
                success=False,
                message=f"订阅不存在或无权修改 (ID: {sub_id})",
            )

        return CommandResult(
            success=True,
            message=f"已更新订阅选项 (ID: {sub_id})",
            data=SubscriptionDTO(
                id=subscription.id,
                user_id=subscription.user_id,
                feed_id=subscription.feed_id,
                title=subscription.title,
                tags=subscription.tags,
                target_session=subscription.target_session,
                platform_name=subscription.platform_name,
                state=subscription.state,
                created_at=subscription.created_at,
                updated_at=subscription.updated_at,
            ),
        )

    async def _apply_ai_comment_option(
        self,
        normalized_options: dict,
        spec: Any,
        sub_id: int,
        user_id: str,
    ) -> str | None:
        """根据 ai_comment 开关 reconcile handlers，返回错误消息或 None。

        spec 接受 ``True/False`` 或 ``{"enabled": bool, "prompt": str}``。
        """
        if isinstance(spec, bool):
            enabled, prompt = spec, None
        elif isinstance(spec, dict):
            enabled = bool(spec.get("enabled"))
            prompt = str(spec.get("prompt") or "").strip() or None
        else:
            return None

        base = normalized_options.get("handlers")
        mode = normalized_options.get("handlers_mode")
        if base is None or mode is None:
            current = await self._subscription_repo.get_by_id(sub_id)
            if not current or current.user_id != user_id:
                return f"订阅不存在或无权修改 (ID: {sub_id})"
            if base is None:
                base = current.get_handlers()
            if mode is None:
                mode = current.handlers_mode

        # 处理链整体禁用时开关不生效，保持现状。
        if str(mode or "").strip().lower() == "disabled":
            return None

        user_handlers: list[dict[str, Any]] = []
        if enabled and self._get_user_settings_cmd is not None:
            user_handlers = await self._fetch_user_handlers(user_id)

        normalized_options["handlers"] = _reconcile_ai_comment_handlers(
            base or [],
            enabled=enabled,
            prompt=prompt,
            user_handlers=user_handlers,
            mode=str(mode or "").strip().lower(),
        )
        return None

    async def _apply_merge_condition_option(
        self,
        normalized_options: dict,
        spec: Any,
        sub_id: int,
        user_id: str,
    ) -> str | None:
        """根据「合并转发条件」开关 reconcile handlers，返回错误消息或 None。

        spec 接受 ``True/False`` 或
        ``{"enabled": bool, "max_chars": int, "max_images": int}``。
        """
        if isinstance(spec, bool):
            enabled, max_chars, max_images = spec, None, None
        elif isinstance(spec, dict):
            enabled = bool(spec.get("enabled"))
            max_chars = _coerce_optional_int(spec.get("max_chars"))
            max_images = _coerce_optional_int(spec.get("max_images"))
        else:
            return None

        base = normalized_options.get("handlers")
        mode = normalized_options.get("handlers_mode")
        if base is None or mode is None:
            current = await self._subscription_repo.get_by_id(sub_id)
            if not current or current.user_id != user_id:
                return f"订阅不存在或无权修改 (ID: {sub_id})"
            if base is None:
                base = current.get_handlers()
            if mode is None:
                mode = current.handlers_mode

        # 处理链整体禁用时开关不生效，保持现状。
        if str(mode or "").strip().lower() == "disabled":
            return None

        user_handlers: list[dict[str, Any]] = []
        if enabled and self._get_user_settings_cmd is not None:
            user_handlers = await self._fetch_user_handlers(user_id)

        normalized_options["handlers"] = _reconcile_merge_condition_handlers(
            base or [],
            enabled=enabled,
            max_chars=max_chars,
            max_images=max_images,
            user_handlers=user_handlers,
            mode=str(mode or "").strip().lower(),
        )
        return None

    async def _fetch_user_handlers(self, user_id: str) -> list[dict[str, Any]]:
        """读取用户当前全局 handlers 快照；读取失败时返回空列表。"""
        try:
            result = await self._get_user_settings_cmd.execute(user_id=user_id)
        except Exception:
            return []
        data = getattr(result, "data", None)
        if not isinstance(data, dict):
            return []
        raw = data.get("handlers")
        return list(raw) if isinstance(raw, list) else []
