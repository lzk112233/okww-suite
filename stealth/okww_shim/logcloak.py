"""
logcloak.py — 落盘痕迹抹除

反作弊/风控侧的静态扫描非常便宜：
    扫 %USERPROFILE%\\ok-ww\\logs\\*.log
    扫 程序目录\\configs\\*.json 里的 ok-ww 字样
    扫 Recent / Prefetch / JumpList
只要命中文件名或路径片段就直接判定。

这里做四件事：
  1) 所有 logging.FileHandler 重定向到 %TEMP%\\<rand>\\<rand>.tmp
  2) 文件内容里的可识别字符串做替换（ok-ww / okww / ok-script / opencv 路径）
  3) stdout/stderr 指向 NUL（pythonw 下本来没有控制台，双保险）
  4) 提供 wipe()，退出时删掉整个临时目录
"""
from __future__ import annotations

import atexit
import logging
import os
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

_TOKENS = re.compile(r"(ok[-_ ]?ww|ok[-_ ]?script|okww|ok-ww)", re.I)

_orig_filehandler_init = None
CLOAK_DIR: Path | None = None
_installed = False


def cloak_dir() -> Path:
    global CLOAK_DIR
    if CLOAK_DIR is None:
        CLOAK_DIR = Path(tempfile.gettempdir()) / f".{uuid.uuid4().hex[:8]}"
        CLOAK_DIR.mkdir(parents=True, exist_ok=True)
    return CLOAK_DIR


class _ScrubFilter(logging.Filter):
    """落盘前把可识别字符串抹掉：ok-ww / ok-script / okww -> app"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = scrub(record.msg)
            if record.args:
                if isinstance(record.args, tuple):
                    record.args = tuple(scrub(a) if isinstance(a, str) else a
                                        for a in record.args)
                elif isinstance(record.args, dict):
                    record.args = {k: (scrub(v) if isinstance(v, str) else v)
                                   for k, v in record.args.items()}
        except Exception:
            pass
        return True


class _CloakedFileHandler(logging.FileHandler):
    """任何 FileHandler 都被改写到临时随机目录 + 随机文件名，并过滤特征串。"""

    def __init__(self, filename, mode="a", encoding=None, delay=False, errors=None):
        d = cloak_dir()
        suffix = Path(str(filename)).suffix or ".tmp"
        safe = uuid.uuid4().hex[:12] + suffix
        super().__init__(str(d / safe), mode=mode, encoding=encoding or "utf-8",
                         delay=delay, errors=errors)
        self.addFilter(_ScrubFilter())


def scrub(text: str) -> str:
    return _TOKENS.sub("app", text)


def install(quiet_stdio: bool = True) -> None:
    global _installed, _orig_filehandler_init
    if _installed:
        return

    # logging.FileHandler 覆写（monkeypatch __init__，不动调用方）
    logging.FileHandler = _CloakedFileHandler

    # 常见第三方也走 basicConfig(filename=...)
    _orig_basic = logging.basicConfig

    def _basic(**kw):
        if kw.get("filename"):
            d = cloak_dir()
            kw["filename"] = str(d / (uuid.uuid4().hex[:12] + ".tmp"))
        return _orig_basic(**kw)

    logging.basicConfig = _basic

    # 让 PySide6 / onnxruntime 的 stderr 噪声消失
    if quiet_stdio:
        try:
            null = open(os.devnull, "w", encoding="utf-8", errors="ignore")
            sys.stdout = null
            sys.stderr = null
        except Exception:
            pass

    atexit.register(wipe)
    _installed = True


def wipe() -> None:
    """退出时删除临时日志目录（含 configs 的副本，如果有）。"""
    try:
        if CLOAK_DIR and CLOAK_DIR.exists():
            shutil.rmtree(CLOAK_DIR, ignore_errors=True)
    except Exception:
        pass


def relocate_file(p: str | Path) -> Path:
    """把任意路径搬到 cloak 目录，返回新路径。"""
    src = Path(p)
    dst = cloak_dir() / (uuid.uuid4().hex[:12] + (src.suffix or ".tmp"))
    try:
        if src.exists():
            shutil.move(str(src), str(dst))
            return dst
    except Exception:
        pass
    return src
