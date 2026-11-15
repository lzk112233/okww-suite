"""
qtcloak.py — 窗口/UI 特征抹除

ok-ww 是 PySide6 应用，窗口标题、类名、进程名三件套就能被枚举出来：
    EnumWindows -> GetWindowTextW == "OK-WW"
    GetClassNameW == "Qt5152QWindowIcon"
    GetWindowThreadProcessId -> 进程名 ok-ww.exe / python.exe

对策（三档，从轻到重）：
  light : 只改标题（每次随机业务名），窗口照常显示
  hide  : 不显示窗口，但保留 QApplication 事件循环（截图/推理照跑）
  ghost : QT_QPA_PLATFORM=offscreen，完全不创建原生窗口，桌面上一片干净

本模块自动读 OKWW_CLOAK 环境变量选档。
"""
from __future__ import annotations

import os
import random
import string
import threading
import time

_DECOY = [
    "DeviceSync", "AudioConsole", "BluetoothService", "DisplayAgent",
    "NetworkMonitor", "PowerManager", "InputService", "UpdateBroker",
    "ShellExperience", "SyncHost",
]

_installed = False


def decoy_title() -> str:
    base = random.choice(_DECOY)
    return base + "".join(random.choices(string.ascii_uppercase + string.digits, k=4))


def install(mode: str | None = None) -> str:
    global _installed
    mode = (mode or os.environ.get("OKWW_CLOAK") or "light").lower()
    if mode == "ghost":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

    if _installed:
        return mode
    _installed = True

    def _worker():
        # 等 QApplication 起来再动手，最多等 30s
        app = None
        for _ in range(300):
            try:
                from PySide6.QtWidgets import QApplication
                app = QApplication.instance()
                if app is not None:
                    break
            except Exception:
                try:
                    from PyQt5.QtWidgets import QApplication  # 兼容旧版
                    app = QApplication.instance()
                    if app is not None:
                        break
                except Exception:
                    pass
            time.sleep(0.1)
        if app is None:
            return

        title = decoy_title()
        if mode in ("hide", "ghost"):
            for w in app.topLevelWidgets():
                try:
                    w.hide()
                except Exception:
                    pass

        # 持续守护：任何新建的顶层窗口都改名/隐藏
        while True:
            try:
                for w in app.topLevelWidgets():
                    try:
                        if w.windowTitle() and w.windowTitle() != title:
                            w.setWindowTitle(title)
                        if mode in ("hide", "ghost") and w.isVisible():
                            w.hide()
                    except Exception:
                        pass
            except Exception:
                return
            time.sleep(1.5)

    threading.Thread(target=_worker, name="qtcloak", daemon=True).start()
    return mode
