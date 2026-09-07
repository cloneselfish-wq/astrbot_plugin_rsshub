"""通用 HTTP 抓取器

提供异步 HTTP 请求的基础设施，管理共享 aiohttp session。
"""

from __future__ import annotations

import asyncio
from ssl import SSLError

import aiohttp

from ...application.dto import WebFeed
from ...domain.exceptions import WebError
from ..utils import get_logger
from ..utils.proxy_bypass import resolve_proxy_for_url

logger = get_logger()


class HttpFetcher:
    """通用异步 HTTP 抓取器，管理共享 aiohttp session。"""

    def __init__(self, timeout: int = 30, proxy: str = "") -> None:
        self.timeout = max(1, int(timeout or 30))
        self.proxy = (proxy or "").strip()
        self._session: aiohttp.ClientSession | None = None
        self._session_lock: asyncio.Lock | None = None

    async def close(self) -> None:
        """关闭内部管理的 session。"""
        if self._session_lock is None:
            return
        async with self._session_lock:
            if self._session is not None and not self._session.closed:
                await self._session.close()
            self._session = None

    async def _get_shared_session(self) -> aiohttp.ClientSession:
        if self._session_lock is None:
            self._session_lock = asyncio.Lock()
        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
        return self._session

    async def fetch(
        self,
        url: str,
        *,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
        verbose: bool = True,
        proxy: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> WebFeed:
        """执行 HTTP GET 请求，返回 WebFeed（不含 rss_d）。

        Args:
            url: 请求 URL
            timeout: 请求超时（秒），默认使用实例配置
            headers: 额外请求头
            verbose: 是否输出详细日志
            proxy: 临时代理地址（优先于实例代理）
            session: 外部 session（若提供则直接使用）

        Returns:
            WebFeed 响应结果
        """
        ret = WebFeed(url=url, ori_url=url)
        log_level = 30 if verbose else 10
        effective_proxy = resolve_proxy_for_url(
            (proxy or "").strip() or self.proxy, url
        )

        _headers: dict[str, str] = {}
        if headers:
            _headers.update(headers)

        use_shared = not effective_proxy and session is None
        client: aiohttp.ClientSession
        temp_session: aiohttp.ClientSession | None = None

        try:
            if use_shared:
                client = await self._get_shared_session()
            elif session is not None:
                client = session
            else:
                temp_session = aiohttp.ClientSession(proxy=effective_proxy or None)
                client = temp_session

            async with client.get(
                url,
                headers=_headers,
                timeout=timeout or self.timeout,
                proxy=effective_proxy or None if not use_shared else None,
            ) as resp:
                content = await resp.read()
                ret.content = content
                ret.url = str(resp.url)
                ret.headers = dict(resp.headers.items())
                ret.status = resp.status
                ret.reason = resp.reason

                if (
                    resp.status == 200
                    and int(resp.headers.get("Content-Length", "1")) == 0
                ):
                    ret.status = 304
                    return ret

                if resp.status == 304:
                    return ret

                if content is None or resp.status != 200:
                    status_caption = f"{resp.status}" + (
                        f" {resp.reason}" if resp.reason else ""
                    )
                    ret.error = WebError(
                        error_name="status error",
                        status=status_caption,
                        url=url,
                        log_level=log_level,
                    )
                    return ret

        except aiohttp.InvalidURL:
            ret.error = WebError(error_name="URL invalid", url=url, log_level=log_level)
        except (
            asyncio.TimeoutError,
            aiohttp.ClientError,
            SSLError,
            OSError,
            ConnectionError,
            TimeoutError,
        ) as e:
            ret.error = WebError(
                error_name="network error",
                url=url,
                base_error=e,
                log_level=log_level,
            )
        except Exception as e:
            ret.error = WebError(
                error_name="internal error",
                url=url,
                base_error=e,
                log_level=40,
            )
        finally:
            if temp_session is not None and not temp_session.closed:
                await temp_session.close()

        return ret
