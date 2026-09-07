"""Infrastructure media fingerprint adapter."""

from __future__ import annotations

import hashlib

import aiohttp

from ..utils import get_logger
from ..utils.proxy_bypass import resolve_proxy_for_url

logger = get_logger()


class HttpMediaFingerprintService:
    """Download small media samples and calculate SHA256 fingerprints."""

    def __init__(
        self,
        *,
        timeout_seconds: int = 10,
        max_bytes: int = 5 * 1024 * 1024,
        proxy: str = "",
        max_urls: int = 3,
    ) -> None:
        self._timeout_seconds = max(1, timeout_seconds)
        self._max_bytes = max(1, max_bytes)
        self._proxy = proxy.strip()
        self._max_urls = max(1, max_urls)

    async def fingerprint_urls(self, urls: list[str]) -> list[str]:
        hashes: list[str] = []
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for url in urls[: self._max_urls]:
                digest = await self._fingerprint_url(session, url)
                if digest:
                    hashes.append(f"media:{digest}")
        return hashes

    async def _fingerprint_url(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> str | None:
        if not url.startswith(("http://", "https://")):
            return None

        try:
            effective_proxy = resolve_proxy_for_url(self._proxy, url)
            async with session.get(url, proxy=effective_proxy or None) as resp:
                if resp.status != 200:
                    return None

                size = 0
                sha = hashlib.sha256()
                async for chunk in resp.content.iter_chunked(8192):
                    size += len(chunk)
                    if size > self._max_bytes:
                        return None
                    sha.update(chunk)
                return sha.hexdigest() if size > 0 else None
        except Exception as ex:
            logger.debug("Media fingerprint failed: url=%s, err=%s", url, ex)
            return None
