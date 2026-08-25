"""订阅相关命令处理器（纯函数）"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent

from ...application.services.session_push_queue import SessionPushQueue
from ...domain.entities.handlers import build_ai_comment_handler


def _extract_bot_self_id(event: AstrMessageEvent) -> str:
    """从事件中提取接收消息的 bot self_id，用于合并转发节点身份。

    ``message_obj.self_id`` 是订阅那一刻实际收到指令的那个 bot 的 QQ 号，
    把它存到订阅上，推送合并转发时就能用正确节点的 uin，而不是 bot1。
    """
    msg_obj = getattr(event, "message_obj", None)
    if msg_obj is None:
        return ""
    self_id = getattr(msg_obj, "self_id", None)
    if self_id is None:
        return ""
    sid = str(self_id).strip()
    if not sid or sid == "0":
        return ""
    return sid


async def _fetch_user_handlers(deps: dict, user_id: str) -> list[dict]:
    """读取用户当前全局 handlers 快照；读取失败时返回空列表。"""
    getter = deps.get("get_user_settings_cmd")
    if getter is None:
        return []
    try:
        user_result = await getter.execute(user_id=user_id)
    except Exception:
        return []
    data = getattr(user_result, "data", None)
    if data is None:
        return []
    raw = data.get("handlers") if isinstance(data, dict) else None
    return list(raw) if isinstance(raw, list) else []


def _merge_default_comment_handlers(user_handlers: list[dict]) -> list[dict]:
    """用户全局 handlers + 启用的 ai_comment（已有则不重复）。"""
    merged = list(user_handlers)
    has_comment = any(
        str(item.get("name", "")).strip() == "ai_comment" for item in merged
    )
    if not has_comment:
        merged.extend(build_ai_comment_handler())
    return merged


async def handle_sub(
    event: AstrMessageEvent,
    url: str,
    deps: dict,
    *,
    with_comment: bool = False,
) -> dict:
    """订阅 RSS 源

    ``with_comment=True``（LLM 工具路径）时，把用户当前全局 handlers
    快照进订阅并追加一个启用的 ai_comment，保证继承语义下全局改写需求
    不被订阅自带 handlers 覆盖；用户已有 ai_comment 则不重复添加。
    ``/sub`` 命令保持默认 ``False``，行为不变。
    """
    if not url:
        return {"plain": "请提供 RSS 源的 URL\n用法: /sub <url>"}

    user_id = event.get_sender_id()
    target_session = event.unified_msg_origin
    platform_name = event.get_platform_name()

    url_list = [u.strip() for u in url.split() if u.strip()]
    valid_urls = [u for u in url_list if u.startswith(("http://", "https://"))]

    if not valid_urls:
        return {"plain": "请提供有效的 RSS 链接（需以 http 或 https 开头）"}

    bot_self_id = _extract_bot_self_id(event)

    default_handlers = None
    if with_comment:
        default_handlers = _merge_default_comment_handlers(
            await _fetch_user_handlers(deps, user_id)
        )

    if len(valid_urls) == 1:
        result = await deps["subscribe_cmd"].execute(
            url=valid_urls[0],
            user_id=user_id,
            target_session=target_session,
            platform_name=platform_name,
            bot_self_id=bot_self_id,
            default_handlers=default_handlers,
        )
        return {"plain": result.message}

    results = []
    for single_url in valid_urls:
        r = await deps["subscribe_cmd"].execute(
            url=single_url,
            user_id=user_id,
            target_session=target_session,
            platform_name=platform_name,
            bot_self_id=bot_self_id,
            default_handlers=default_handlers,
        )
        results.append(r)

    success_count = sum(1 for r in results if r.success)
    failed_count = len(results) - success_count

    if failed_count == 0:
        return {"plain": f"成功订阅 {success_count} 个 RSS 源"}
    elif success_count == 0:
        return {"plain": f"全部失败: {failed_count} 个订阅失败"}
    else:
        return {"plain": f"部分成功: {success_count} 个订阅成功, {failed_count} 个失败"}


async def handle_unsub(event: AstrMessageEvent, sub_id: str, deps: dict) -> dict:
    """取消订阅"""
    args = sub_id.strip() if isinstance(sub_id, str) else str(sub_id).strip()
    if not args:
        return {"plain": "请提供订阅 ID 或 URL\n用法: /unsub <ID/URL...>"}

    parts = [p.strip() for p in args.split() if p.strip()]
    if not parts:
        return {"plain": "请提供订阅 ID 或 URL\n用法: /unsub <ID/URL...>"}

    user_id = event.get_sender_id()
    current_session = event.unified_msg_origin
    is_admin = event.is_admin()
    results: list[str] = []
    for token in parts:
        if token.startswith(("http://", "https://")):
            result = await deps["unsubscribe_cmd"].execute_by_url(
                url=token,
                user_id=user_id,
                current_session=current_session,
                is_admin=is_admin,
            )
        else:
            try:
                sub_id_int = int(token)
            except ValueError:
                results.append(f"参数无效: {token}（需为订阅 ID 或 URL）")
                continue
            result = await deps["unsubscribe_cmd"].execute(
                sub_id=sub_id_int,
                user_id=user_id,
                current_session=current_session,
                is_admin=is_admin,
            )
        results.append(result.message)

    return {"plain": "\n".join(results)}


async def handle_sub_list(event: AstrMessageEvent, args: str, deps: dict) -> dict:
    """查看订阅列表"""
    user_id = event.get_sender_id()
    arg_parts = [p.strip() for p in (args or "").split() if p.strip()]

    page = 1
    page_size = 5
    if arg_parts:
        try:
            page = max(1, int(arg_parts[0]))
        except ValueError:
            return {"plain": "分页参数无效。用法: /sub_list [page] [page_size]"}
    if len(arg_parts) >= 2:
        try:
            page_size = max(1, min(int(arg_parts[1]), 100))
        except ValueError:
            return {"plain": "分页参数无效。用法: /sub_list [page] [page_size]"}

    result = await deps["get_subs_query"].execute(user_id=user_id)
    if not result.subscriptions:
        return {"plain": "暂无订阅\n使用 /sub <url> 添加订阅"}

    current_session = event.unified_msg_origin
    visible = [
        sub
        for sub in result.subscriptions
        if (sub.target_session or current_session) == current_session
    ]

    if not visible:
        return {"plain": "当前会话没有订阅\n使用 /sub <url> 添加订阅"}

    total_count = len(visible)
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    page_subs = visible[start : start + page_size]

    active_count = sum(1 for sub in visible if sub.state == 1)
    inactive_count = total_count - active_count
    lines = [
        "您的订阅列表（当前会话）:",
        f"共 {total_count} 个订阅 | 启用: {active_count} | 禁用: {inactive_count}",
        f"页码: {page}/{total_pages}  每页: {page_size}",
    ]
    for i, sub in enumerate(page_subs, start + 1):
        state_icon = "✓" if sub.state == 1 else "✗"
        feed_title = sub.feed_title or f"Feed #{sub.feed_id}"
        custom_title = f" ({sub.title})" if sub.title else ""
        lines.append(f"{i}. [{sub.id}] {state_icon} {feed_title}{custom_title}")
        if sub.feed_link:
            lines.append(f"    {sub.feed_link}")
    return {"plain": "\n".join(lines)}


async def handle_refresh(event: AstrMessageEvent, feed_id: int, deps: dict) -> dict:
    """刷新订阅"""
    if feed_id <= 0:
        return {"plain": "请提供 Feed ID\n用法: /refresh <feed_id>"}
    result = await deps["polling_service"].poll_feed(feed_id)
    return {"plain": result.message}


def handle_rss_stop(
    event: AstrMessageEvent, queue: SessionPushQueue, args: str
) -> dict:
    """停止当前会话 RSS 推送任务（运行中/排队中）。"""
    current_session = event.unified_msg_origin
    target = (args or "").strip()

    if not target:
        result = queue.stop_current(current_session)
        if result.stopped:
            queued = (
                f"，队列中还有 {result.queued_count} 个任务"
                if result.queued_count
                else ""
            )
            return {"plain": f"{result.message}{queued}"}
        return {"plain": result.message}

    if target.lower() == "all":
        stopped = queue.stop_all_for_session(current_session)
        if stopped["stopped"] <= 0:
            return {"plain": "当前会话没有可停止的任务"}
        return {
            "plain": (
                "已请求停止当前会话任务: "
                f"总计 {stopped['stopped']} 个 "
                f"(running={stopped['running']}, queued={stopped['queued']})"
            )
        }

    if target.isdigit():
        result = queue.stop_by_feed_id(current_session, int(target))
    else:
        result = queue.stop_by_job_id(current_session, target)
    return {"plain": result.message}


def handle_sub_status(event: AstrMessageEvent, queue: SessionPushQueue) -> dict:
    """查看当前会话推送任务状态（运行中与排队中）。"""
    current_session = event.unified_msg_origin
    jobs = queue.get_jobs(current_session)
    if not jobs:
        return {"plain": "当前会话没有推送任务"}

    lines = [f"当前会话任务数: {len(jobs)}"]
    for idx, job in enumerate(jobs, start=1):
        feed_label = (
            f"{job.feed_title or '(未知Feed)'}"
            f" | feed_id={job.feed_id if job.feed_id is not None else '未知'}"
        )
        lines.append(f"{idx}. {job.status} | job_id={job.job_id} | {feed_label}")
    lines.append("可用: /sub_stop <job_id|feed_id|all>")
    return {"plain": "\n".join(lines)}


async def handle_sub_state(
    event: AstrMessageEvent, sub_id_str: str, deps: dict
) -> dict:
    """订阅状态管理"""
    if not sub_id_str:
        return {"plain": "用法: /sub_state <订阅ID> on/off\n示例: /sub_state 123 on"}

    parts = sub_id_str.split()
    if len(parts) < 2:
        return {"plain": "用法: /sub_state <订阅ID> on/off\n示例: /sub_state 123 on"}

    try:
        sub_id = int(parts[0])
    except ValueError:
        return {"plain": "订阅 ID 必须是数字"}

    state_str = parts[1].lower()
    if state_str in ("on", "true", "yes", "y", "1", "开启"):
        enable = True
    elif state_str in ("off", "false", "no", "n", "0", "关闭"):
        enable = False
    else:
        return {
            "plain": "不支持的状态值，请使用: on/off, true/false, yes/no, y/n, 1/0, 开启/关闭"
        }

    user_id = event.get_sender_id()
    result = await deps["sub_state_cmd"].execute(
        sub_id=sub_id, user_id=user_id, enable=enable
    )
    return {"plain": result.message}
