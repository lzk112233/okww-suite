"""
okww_shim — 运行期抹除层（对 ok-ww 零改动注入）

install() 做的事：
  1) 把 sys.modules 里的 mss / pydirectinput / pyautogui / mouse 换成人类化实现
  2) 日志落盘重定向到临时随机目录，退出即删
  3) 断掉自更新/统计的外连（DNS 层拦截）
  4) 窗口标题随机化 / 隐藏 / offscreen
  5) requests 默认 UA 改成浏览器 UA

用法（两种，任选）：
  A. 环境变量注入（推荐，不改 ok-ww 一个字）
       set PYTHONPATH=D:\\ai\\dfy\\okww-stealth
       python -m ok            # sitecustomize.py 会自动装
  B. 在入口最前面手动
       import okww_shim; okww_shim.install()
"""
from __future__ import annotations

import os
import sys
import types

__version__ = "1.0.0"

from . import behavior, capture, hid_bridge, logcloak, netblock, qtcloak  # noqa: F401

_INSTALLED = False


# ── 伪模块：把任意属性转发到 human_input ──

class _HumanProxy(types.ModuleType):
    _SELF_ATTRS = {"__file__", "__loader__", "__spec__", "__name__"}

    def __init__(self, name: str, target):
        super().__init__(name)
        self.__dict__["_target"] = target
        self.__dict__["__file__"] = f"<okww_shim.proxy:{name}>"

    def __getattr__(self, item):
        if item.startswith("__") and item.endswith("__") and item not in self._SELF_ATTRS:
            raise AttributeError(item)
        return getattr(self.__dict__["_target"], item)


class _MouseProxy(types.ModuleType):
    """`pip install mouse` 那套 API：mouse.move(x, y, absolute=True) / mouse.click()"""

    def __init__(self, target):
        super().__init__("mouse")
        self.__dict__["_t"] = target
        self.__dict__["__file__"] = "<okww_shim.proxy:mouse>"

    def move(self, x, y, absolute=True, duration=0):
        if absolute:
            self._t.moveTo(x, y)
        else:
            self._t.moveRel(x, y)

    def click(self, button="left", count=1):
        self._t.click(button=button, clicks=count)

    def press(self, button="left"):
        self._t.mouseDown(button=button)

    def release(self, button="left"):
        self._t.mouseUp(button=button)

    def wheel(self, dy):
        self._t.scroll(int(dy))

    def __getattr__(self, item):
        return getattr(self._t, item)


def _patch_requests_ua() -> None:
    try:
        import requests
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
        _orig = requests.Session.__init__

        def _init(self, *a, **kw):
            _orig(self, *a, **kw)
            self.headers["User-Agent"] = ua
            self.headers.pop("Accept-Encoding", None)
            self.headers["Accept-Encoding"] = "gzip, deflate, br"

        requests.Session.__init__ = _init
    except Exception:
        pass


def install(cloak: str | None = None, block_net: bool = True,
            quiet_stdio: bool = True, prefer_backend: str | None = None) -> dict:
    global _INSTALLED
    report = {}
    if _INSTALLED:
        return {"already": True}

    if prefer_backend:
        os.environ["OKWW_INPUT_BACKEND"] = prefer_backend

    # 1) 输入层
    try:
        from . import human_input
        sys.modules["pydirectinput"] = human_input
        sys.modules["pydirectinput33"] = human_input
        sys.modules["pyautogui"] = _HumanProxy("pyautogui", human_input)
        sys.modules["mouse"] = _MouseProxy(human_input)
        report["input"] = human_input.get().backend.name
    except Exception as e:
        report["input"] = f"fail: {e}"

    # 2) 采集层
    try:
        sys.modules["mss"] = capture
        report["capture"] = "patched"
    except Exception as e:
        report["capture"] = f"fail: {e}"

    # 3) 落盘
    try:
        logcloak.install(quiet_stdio=quiet_stdio)
        report["logs"] = str(logcloak.cloak_dir())
    except Exception as e:
        report["logs"] = f"fail: {e}"

    # 4) 外连
    if block_net:
        try:
            netblock.install()
            report["net"] = "blocked"
        except Exception as e:
            report["net"] = f"fail: {e}"

    # 5) 窗口
    try:
        report["window"] = qtcloak.install(cloak)
    except Exception as e:
        report["window"] = f"fail: {e}"

    # 6) UA
    _patch_requests_ua()

    _INSTALLED = True
    report["ok"] = True
    return report


def status() -> dict:
    return {
        "installed": _INSTALLED,
        "modules": {n: getattr(sys.modules.get(n), "__file__", "?")
                    for n in ("mss", "pydirectinput", "pyautogui", "mouse")
                    if n in sys.modules},
        "cloak_dir": str(logcloak.CLOAK_DIR) if logcloak.CLOAK_DIR else None,
        "net_blocked": sorted(netblock.BLOCKED)[:5],
    }
