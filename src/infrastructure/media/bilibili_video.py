"""B站视频解析模块（bilibili_rss 分支定制）

把 feed 条目中的 BV 号解析为可直接下载的 MP4 地址：
    BV 号 --view API--> cid --playurl API--> mp4 单文件直链

说明：
- 未登录 Cookie 时 B站返回 360P（实测 qn 参数会被降级到 16）；
  配置 bilibili.cookie（SESSDATA）后可提升清晰度。
- api.bilibili.com 与 bilivideo.com 均命中 no_proxy 直连名单，
  下载链路复用现有 MediaDownloader（含大小限额/转码/NapCat 单发）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import aiohttp

from astrbot.core.utils.http_ssl import build_tls_connector

from ..utils.logger import get_logger
from ..utils.proxy_bypass import resolve_proxy_for_url

logger = get_logger()

# 标准 BV 号：BV + 10 位 Base58 字符（含大小写与数字）
BV_ID_PATTERN = re.compile(r"BV[0-9A-Za-z]{10}")

_VIEW_API = "https://api.bilibili.com/x/web-interface/view?bvid={bv}"
_PLAY_API = (
    "https://api.bilibili.com/x/player/playurl"
    "?bvid={bv}&cid={cid}&qn={qn}&platform=html5"
)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def extract_bv_ids(text: str) -> list[str]:
    """从文本中提取去重后的 BV 号，保持出现顺序。"""
    if not text:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for match in BV_ID_PATTERN.finditer(text):
        bv = match.group(0)
        if bv not in seen:
            seen.add(bv)
            result.append(bv)
    return result


@dataclass(frozen=True)
class ResolvedBilibiliVideo:
    """解析结果：可直接交给媒体下载器的 MP4 直链及元信息。"""

    bv_id: str
    video_url: str
    size_bytes: int
    duration_seconds: int
    title: str
    quality: int


class BilibiliVideoResolver:
    """BV 号 → MP4 直链解析器。

    Args:
        cookie: B站 Cookie（至少含 SESSDATA），留空为未登录（360P）
        qn: 期望清晰度（16=360P 32=480P 64=720P 80=1080P），
            实际清晰度受 Cookie 权限限制，B站会自动降级
        timeout_seconds: 单次 API 请求超时
        proxy: 配置的 HTTP 代理；api.bilibili.com 命中 no_proxy 会自动直连
    """

    def __init__(
        self,
        *,
        cookie: str = "",
        qn: int = 32,
        timeout_seconds: int = 20,
        proxy: str = "",
    ) -> None:
        self._cookie = str(cookie or "").strip()
        self._qn = max(16, int(qn or 32))
        self._timeout_seconds = max(5, int(timeout_seconds or 20))
        self._proxy = str(proxy or "").strip()

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": _UA,
            "Referer": "https://www.bilibili.com/",
        }
        if self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    async def resolve(self, bv_id: str) -> ResolvedBilibiliVideo | None:
        """解析 BV 号；任何一步失败返回 None（由调用方决定重试策略）。"""
        bv = str(bv_id or "").strip()
        if not BV_ID_PATTERN.fullmatch(bv):
            return None
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout_seconds),
                trust_env=True,
                connector=build_tls_connector(),
            ) as session:
                view = await self._get_json(
                    session, _VIEW_API.format(bv=bv), "view"
                )
                if view.get("code") != 0:
                    logger.warning(
                        "[bilibili] view API 失败: bv=%s, code=%s, msg=%s",
                        bv,
                        view.get("code"),
                        view.get("message"),
                    )
                    return None
                data = view.get("data") or {}
                cid = data.get("cid")
                if not cid:
                    logger.warning("[bilibili] view API 无 cid: bv=%s", bv)
                    return None

                play = await self._get_json(
                    session,
                    _PLAY_API.format(bv=bv, cid=cid, qn=self._qn),
                    "playurl",
                )
                if play.get("code") != 0:
                    logger.warning(
                        "[bilibili] playurl API 失败: bv=%s, code=%s, msg=%s",
                        bv,
                        play.get("code"),
                        play.get("message"),
                    )
                    return None
                play_data = play.get("data") or {}
                durl = play_data.get("durl") or []
                if not durl:
                    logger.warning("[bilibili] playurl 无 durl（可能返回 DASH）: bv=%s", bv)
                    return None
                video_url = str(durl[0].get("url") or "").strip()
                if not video_url:
                    logger.warning("[bilibili] playurl durl 无 url: bv=%s", bv)
                    return None

                return ResolvedBilibiliVideo(
                    bv_id=bv,
                    video_url=video_url,
                    size_bytes=int(durl[0].get("size") or 0),
                    duration_seconds=int(data.get("duration") or 0),
                    title=str(data.get("title") or ""),
                    quality=int(play_data.get("quality") or 0),
                )
        except Exception as exc:
            logger.warning(
                "[bilibili] 解析视频失败: bv=%s, err_type=%s, err=%r",
                bv,
                type(exc).__name__,
                exc,
            )
            return None

    async def _get_json(
        self, session: aiohttp.ClientSession, url: str, tag: str
    ) -> dict:
        proxy = resolve_proxy_for_url(self._proxy, url)
        async with session.get(
            url,
            proxy=proxy or None,
            headers=self._headers(),
        ) as resp:
            if resp.status >= 400:
                return {"code": -resp.status, "message": f"{tag} http {resp.status}"}
            return await resp.json(content_type=None)
