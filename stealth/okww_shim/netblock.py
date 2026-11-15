"""
netblock.py — 外连特征切断

ok-ww 会做三件对外可见的事：
  1) 检查更新（拉 github raw / ok-script.com 的 version.json）
  2) 匿名统计（UA 里带 python-requests / ok-ww 版本）
  3) 下载模型

这三条在运营商侧和风控侧都是可关联的指纹：同一 IP 上稳定出现
"python-requests 拉 ok-script.com" 就是脚本特征。这里在 DNS 层直接
把相关域名解析到 127.0.0.1，连接立刻失败，不产生任何外连流量。

注意：只拦脚本自身的域名，不影响游戏客户端。
"""
from __future__ import annotations

import socket

DEFAULT_BLOCK = {
    "ok-script.com",
    "api.ok-script.com",
    "update.ok-script.com",
    "ok-ww.com",
    "okww.repo",
    "raw.githubusercontent.com",   # 模型/配置 raw 拉取
    "objects.githubusercontent.com",
    "api.github.com",
    "github.com",
    "gitee.com",
}

_orig_gai = None
_orig_ghbn = None
_installed = False
BLOCKED: set[str] = set()
LOG: list[str] = []


def _match(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    for d in BLOCKED:
        if host == d or host.endswith("." + d):
            return True
    return False


def install(domains=None) -> None:
    global _orig_gai, _orig_ghbn, _installed
    if _installed:
        return
    BLOCKED.update(domains or DEFAULT_BLOCK)
    _orig_gai = socket.getaddrinfo
    _orig_ghbn = socket.gethostbyname

    def _gai(host, port, *a, **kw):
        if _match(host):
            LOG.append(f"blocked getaddrinfo {host}:{port}")
            raise socket.gaierror(-2, f"blocked {host}")
        return _orig_gai(host, port, *a, **kw)

    def _ghbn(host):
        if _match(host):
            LOG.append(f"blocked gethostbyname {host}")
            raise socket.gaierror(-2, f"blocked {host}")
        return _orig_ghbn(host)

    socket.getaddrinfo = _gai
    socket.gethostbyname = _ghbn
    _installed = True


def uninstall() -> None:
    global _installed
    if _installed and _orig_gai:
        socket.getaddrinfo = _orig_gai
        socket.gethostbyname = _orig_ghbn
        _installed = False
