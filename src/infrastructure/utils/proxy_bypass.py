"""按目标 host 的精细代理分流（NO_PROXY 语义）。

AstrBot 侧代理策略对齐 RSSHub 容器（rsshub-rsshub-1 的 NO_PROXY）：
- docker 内部网络 / 私有网段 / 单标签主机名（如 rsshub-rsshub-1）→ 始终强制直连（代码兜底，不可配置，避免代理劫持内网导致 502）
- 配置项 http_config.no_proxy 中的域名 → 直连（完整权威名单，用户可在面板增删）
- 其余目标（twitter、video.twimg.com、youtube 等）→ 走 http_config.proxy

名单语义：
- DEFAULT_NO_PROXY_PATTERNS 仅为“缺省清单”（settings_builder 在配置缺失/留空时回退它），
  不是强制名单——用户配置非空时以用户配置为准。
- 私有网段与单标签主机名规则始终生效，与名单无关，无法通过配置关闭。
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from ipaddress import ip_address, ip_network
from typing import Iterable
from urllib.parse import urlparse

# 缺省直连名单（settings_builder 在 http_config.no_proxy 缺失/为空时回退到它）。
# 默认对齐 RSSHub 容器 NO_PROXY 中的国内站点部分：B 站全系 + 本地 mDNS。
# 注意：localhost / 127.0.0.1 / 私有网段等已由下方强制规则覆盖，无需（也不应）出现在此名单。
DEFAULT_NO_PROXY_PATTERNS: tuple[str, ...] = (
    "*.local",
    # 国内站点（B 站全系：API / 封面图 CDN / 视频流）
    "*.bilibili.com",
    "*.hdslb.com",
    "*.bilivideo.com",
)

# 兜底私有网段（即使配置被清空也强制直连，避免代理劫持内网请求）。
_PRIVATE_NETWORKS: tuple[ip_network, ...] = (
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("127.0.0.0/8"),
    ip_network("169.254.0.0/16"),
)

_configured_patterns: tuple[str, ...] = DEFAULT_NO_PROXY_PATTERNS


def configure_no_proxy(patterns: Iterable[str] | None) -> None:
    """注入完整的 NO_PROXY 名单（覆盖式，全部以小写保存）。

    Args:
        patterns: 权威直连名单。传入 None/空时回退 DEFAULT_NO_PROXY_PATTERNS。
    """
    global _configured_patterns
    normalized: list[str] = []
    for pattern in patterns or ():
        pattern = str(pattern or "").strip().lower()
        if pattern and pattern not in normalized:
            normalized.append(pattern)
    _configured_patterns = tuple(normalized) or DEFAULT_NO_PROXY_PATTERNS


def _host_from_url(url: str) -> str:
    if not url:
        return ""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _is_private_ip(host: str) -> bool:
    try:
        addr = ip_address(host)
    except ValueError:
        return False
    return any(addr in network for network in _PRIVATE_NETWORKS)


def _pattern_matches(host: str, pattern: str) -> bool:
    if fnmatchcase(host, pattern):
        return True
    # "*.example.com" 同时覆盖裸域 example.com（与 curl NO_PROXY 语义一致）
    if pattern.startswith("*."):
        return fnmatchcase(host, pattern[2:])
    # 无通配符的域名形态 pattern 同时匹配其子域，如 bilibili.com 命中 api.bilibili.com
    if "*" not in pattern and "." in pattern:
        return fnmatchcase(host, f"*.{pattern}")
    return False


def should_bypass_proxy(url: str) -> bool:
    """判断给定 URL 是否应绕过代理直连。"""
    host = _host_from_url(url)
    if not host:
        # 无法解析 host（空/畸形 URL）时保守直连
        return True
    if _is_private_ip(host):
        return True
    if ":" not in host and "." not in host:
        # 单标签主机名（docker-compose 服务名、localhost、内网别名）
        return True
    for pattern in _configured_patterns:
        if _pattern_matches(host, pattern):
            return True
    return False


def resolve_proxy_for_url(proxy: str | None, url: str) -> str:
    """返回请求该 URL 时应使用的代理地址；需要直连时返回空串。

    Args:
        proxy: 配置的全局代理地址（可含 http:// 前缀或为裸 host:port）
        url: 本次请求的目标 URL

    Returns:
        生效的代理字符串（"" 表示直连）
    """
    effective = str(proxy or "").strip()
    if not effective:
        return ""
    if should_bypass_proxy(url):
        return ""
    return effective
