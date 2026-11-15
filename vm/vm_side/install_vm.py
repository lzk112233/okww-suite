"""
install_vm.py — 虚拟机侧的胶水层：把采集卡 + 网络 HID 桥接进 ok-ww

它做四件事，全程 monkey-patch，不改 ok-ww 一行代码：
  1) 装 okww_shim（轨迹/时序/日志抹除/联网拦截），拿人类化输入算法
  2) 把 `mss` 换成采集卡后端 —— 虚拟机截不到游戏的画面
  3) 把输入后端换成 net_hid —— 按键从树莓派/板子的 USB 口出海
  4) **虚拟光标记账** —— HID 是相对位移，虚拟机拿不到游戏机的真实光标，
     所以自己维护一套绝对坐标，并 patch position()/size() 让 ok-ww 的
     坐标系等于采集卡画面的坐标系

坐标这件事最容易踩：
  ok-ww 从截图里认出目标 → 得到的是采集卡画面里的像素
  它调 moveTo(x, y) → 需要"从游戏机光标当前位置"走过去的相对位移
  游戏机的光标在哪，虚拟机的 GetCursorPos 根本不知道
  所以：记账 + 启动时 home() 顶到左上角归零

用法
----
    set OKWW_HID_HOST=192.168.1.50
    set OKWW_HID_PORT=8765
    set PYTHONPATH=D:\\ai\\dfy\\okww-vm\\vm_side;D:\\ai\\dfy\\okww-stealth
    python sitecustomize 自动执行，或手动：

    from install_vm import install_vm
    st = install_vm()
    print(st)
"""
from __future__ import annotations

import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_STEALTH = os.environ.get("OKWW_STEALTH", r"D:\ai\dfy\okww-stealth")


# ────────────────────── 虚拟光标 ──────────────────────

class VirtualCursor:
    """记账式绝对坐标。坐标系 = 采集卡画面（= 游戏机输出分辨率）。"""

    def __init__(self, width: int, height: int):
        self.w = int(width)
        self.h = int(height)
        self.x = 0.0
        self.y = 0.0
        self.clamped = 0        # 撞边次数，撞多了说明记账已经不可信

    def pos(self) -> tuple[int, int]:
        return int(round(self.x)), int(round(self.y))

    def apply_rel(self, dx: int, dy: int) -> None:
        nx, ny = self.x + dx, self.y + dy
        if nx < 0 or nx > self.w - 1 or ny < 0 or ny > self.h - 1:
            self.clamped += 1
        self.x = min(max(nx, 0), self.w - 1)
        self.y = min(max(ny, 0), self.h - 1)

    def set(self, x: float, y: float) -> None:
        self.x = min(max(float(x), 0), self.w - 1)
        self.y = min(max(float(y), 0), self.h - 1)

    def __repr__(self):
        return f"<cursor {self.x:.0f},{self.y:.0f} in {self.w}x{self.h} clamp={self.clamped}>"


class _TrackedBackend:
    """包住真后端：先记账再出海。其余属性透传。"""

    def __init__(self, inner, cursor: VirtualCursor):
        object.__setattr__(self, "inner", inner)
        object.__setattr__(self, "cursor", cursor)

    @property
    def name(self):
        return getattr(self.inner, "name", "unknown")

    def rel(self, dx: int, dy: int) -> None:
        self.cursor.apply_rel(int(dx), int(dy))
        self.inner.rel(dx, dy)

    def __getattr__(self, item):
        return getattr(self.inner, item)

    def __setattr__(self, k, v):
        # 允许外部改 inner 上的东西（比如 reconnect 开关）
        if k in ("inner", "cursor"):
            object.__setattr__(self, k, v)
        else:
            setattr(self.inner, k, v)


# ────────────────────── mss 模块注入 ──────────────────────

def _install_capture(card_device: int, view_w: int, view_h: int, fps: int):
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    import capture_card

    cap = capture_card.mss(device=card_device, width=view_w, height=view_h, fps=fps)
    mod = types.ModuleType("mss")
    mod.mss = capture_card.mss
    mod.__file__ = os.path.join(_HERE, "capture_card.py")
    mod.__loader__ = None
    mod.__spec__ = None
    sys.modules["mss"] = mod
    return cap, mod


# ────────────────────── okww_shim ──────────────────────

def _import_shim(stealth_dir: str):
    if not os.path.isdir(stealth_dir):
        return None
    if stealth_dir not in sys.path:
        sys.path.append(stealth_dir)     # append：别抢我们自己 vm_side 的优先级
    try:
        import okww_shim
        return okww_shim
    except Exception:
        return None


def _import_hi():
    try:
        from okww_shim import human_input as hi
        return hi
    except Exception:
        return None


# ────────────────────── 主入口 ──────────────────────

def install_vm(hid_host: str | None = None,
               hid_port: int | None = None,
               card_device: int | None = None,
               view_w: int | None = None,
               view_h: int | None = None,
               fps: int | None = None,
               stealth_dir: str | None = None,
               home: bool = True,
               cloak: str = "hidden",
               block_net: bool = True,
               quiet_stdio: bool = True) -> dict:
    """一次性装好。返回状态 dict（失败项在里面，不抛异常）。

    quiet_stdio=True 会把 ok-ww 的 stdout/stderr 也静音（生产环境要这样，
    不然控制台里全是它自己的输出）。调试或自检时传 False，否则你自己
    的 print 也会一起消失。
    """
    hid_host = hid_host or os.environ.get("OKWW_HID_HOST", "")
    hid_port = int(hid_port or os.environ.get("OKWW_HID_PORT", 8765))
    card_device = int(card_device if card_device is not None
                      else os.environ.get("OKWW_CARD_DEVICE", 0))
    fps = int(fps or os.environ.get("OKWW_CARD_FPS", 60))
    stealth_dir = stealth_dir or DEFAULT_STEALTH

    status: dict = {"ok": False, "hid": "", "capture": "", "cursor": "",
                    "shim": "", "warnings": []}

    # 1) okww_shim 先装 —— 它会自己接管 sys.modules["mss"]（指向它的 capture.py，
    #    抓的是虚拟机桌面）。所以顺序必须是 shim 在前、采集卡在后覆盖，反过来会被盖掉。
    shim = _import_shim(stealth_dir)
    if shim is not None:
        try:
            r = shim.install(cloak=cloak, block_net=block_net,
                             quiet_stdio=quiet_stdio, prefer_backend="sendinput")
            status["shim"] = str(r)
        except Exception as e:
            status["shim"] = f"install FAILED: {e}"
    else:
        status["shim"] = f"没找到 okww_shim（{stealth_dir}）"
        status["warnings"].append("没装 okww_shim：轨迹/时序拟人化缺失，只有裸输入")

    # 2) 采集卡：覆盖上一步装进去的 mss，并决定坐标系尺寸
    cap = None
    try:
        cap, _ = _install_capture(card_device, view_w or 1920, view_h or 1080, fps)
        cw = cap.monitors[1]["width"]
        ch = cap.monitors[1]["height"]
        status["capture"] = f"card#{card_device} {cw}x{ch} @{cap.fps:.0f}"
        if shim is not None and "capture" in status["shim"]:
            status["warnings"].append("已用采集卡覆盖 okww_shim 的 mss（否则抓到的是 VM 桌面）")
    except Exception as e:
        status["capture"] = f"FAILED: {e}"
        cw, ch = int(view_w or 1920), int(view_h or 1080)
        status["warnings"].append("采集卡没起来，坐标系退回假设值")

    # 3) 输入后端换成 net_hid
    hi = _import_hi()
    cursor = VirtualCursor(cw, ch)
    backend = None
    if hid_host:
        try:
            from net_hid import NetHidBackend
            backend = NetHidBackend(hid_host, hid_port)
            status["hid"] = f"net_hid {hid_host}:{hid_port}"
        except Exception as e:
            status["hid"] = f"FAILED: {e}"
    else:
        status["hid"] = "没给 OKWW_HID_HOST"
        status["warnings"].append("没有 HID 桥地址：输入不会真正送达游戏机")

    if hi is not None:
        h = hi.get()
        if backend is not None:
            try:
                old = getattr(h, "backend", None)
                if old is not None:
                    try:
                        old.close()
                    except Exception:
                        pass
            except Exception:
                pass
            h.backend = _TrackedBackend(backend, cursor)
        elif isinstance(getattr(h, "backend", None), object):
            # 没桥也要记账，不然 position() 会用 VM 的真实光标，坐标全错
            h.backend = _TrackedBackend(h.backend, cursor)

        # 让 ok-ww 看到的世界 = 采集卡画面
        h.position = lambda: cursor.pos()
        h.size = lambda: (cursor.w, cursor.h)
        status["cursor"] = repr(cursor)

        # 4) 归零：把游戏机光标顶到左上角，记账才可信
        if home and backend is not None:
            try:
                _home(cursor, h)
            except Exception as e:
                status["warnings"].append(f"home 失败: {e}")
    else:
        status["warnings"].append("没拿到 human_input：ok-ww 若直连 ctypes 发键，本层不生效")

    # ok 只由"输入链路是否真的接到游戏机"决定。采集卡在没插真卡时必然是
    # FAILED，那不是配置错误，是硬件还没到——所以不能让它把 ok 拖成 False。
    status["ok"] = backend is not None
    status["capture_ok"] = cap is not None
    if cap is None:
        status["warnings"].append(
            "没有采集卡：画面拿不到，坐标是假设的 %dx%d。插上 USB 采集卡并直通进"
            "本虚拟机后重跑即可（OKWW_CARD_DEVICE=N 选设备）" % (cw, ch))
    return status


def _home(cursor: VirtualCursor, h) -> None:
    """把光标推到左上角。多推一点确保撞边，然后记账归零。"""
    h.backend.rel(-(cursor.w + 200), -(cursor.h + 200))
    cursor.set(0, 0)


def status_vm() -> dict:
    """运行中查状态。"""
    hi = _import_hi()
    out: dict = {"mss": "", "cursor": "", "hid": ""}
    mod = sys.modules.get("mss")
    if mod is not None:
        out["mss"] = getattr(mod, "__file__", "?")
    if hi is not None:
        h = hi.get()
        bk = getattr(h, "backend", None)
        out["hid"] = getattr(bk, "name", type(bk).__name__ if bk else "None")
        cur = getattr(bk, "cursor", None)
        out["cursor"] = repr(cur) if cur else "无记账（会被 VM 真实光标污染）"
        try:
            out["size"] = h.size()
            out["position"] = h.position()
        except Exception as e:
            out["size"] = out["position"] = f"ERR {e}"
    return out


if __name__ == "__main__":
    import json

    print(json.dumps(install_vm(), ensure_ascii=False, indent=2))
    print(json.dumps(status_vm(), ensure_ascii=False, indent=2))
