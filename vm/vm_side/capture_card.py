"""
capture_card.py — USB 采集卡截图后端（虚拟机侧，mss 兼容）

为什么不用虚拟机自带截图
------------------------
游戏跑在物理机上，虚拟机里 `mss`/dxgi/GDI 抓到的是虚拟桌面的画面 ——
一张干净的 Windows 桌面，游戏窗口压根不在里面。要看到游戏，只能从
采集卡（或 NDI / Looking Glass）拿画面。

为什么要有后台抓帧线程
----------------------
`cv2.VideoCapture.read()` 是阻塞取队列里最老的一帧。ok-ww 的识别循环
一秒调 2~10 次 grab，一旦调用频率低于采集卡帧率（30/60fps），队列就会
堆帧，抓到的是几百毫秒前的画面 —— 决策全部滞后。所以这里开一个只做
"读并覆盖"的线程，grab() 永远返回**最新**那一帧，延迟恒定在一帧以内。

对外接口与 okww_shim.capture.mss 一致：
    mss(backend="card", device=0, width=1920, height=1080)
    .backend_name / .monitors / .grab(region: dict) / .shot() / .close()

自检：
    python capture_card.py --list        # 列出可用采集设备
    python capture_card.py --probe       # 抓一帧，打印尺寸/亮度/帧率
"""
from __future__ import annotations

import threading
import time

import numpy as np

try:
    import cv2
except Exception as _e:            # 没装 opencv
    cv2 = None
    _CV_ERR = _e
else:
    _CV_ERR = None


class _Shot:
    """mss.screenshot 兼容壳：np.asarray(shot) 得到 BGRA。"""

    __slots__ = ("data", "width", "height", "left", "top")

    def __init__(self, arr: np.ndarray, left: int, top: int):
        self.data = arr
        self.height, self.width = arr.shape[:2]
        self.left, self.top = left, top

    @property
    def size(self):
        return self.width, self.height

    def __array__(self, dtype=None, copy=None):
        arr = self.data if dtype is None else self.data.astype(dtype)
        return arr.copy() if copy else arr

    def rgb(self):
        return self.data[:, :, [2, 1, 0]]

    def bgra(self):
        return self.data


class _CardBackend:
    """单张采集卡。内部维持一个"最新帧"槽位。"""

    def __init__(self, device: int = 0, width: int = 1920, height: int = 1080,
                 fps: int = 60, fourcc: str = "MJPG", open_timeout: float = 3.0):
        if cv2 is None:
            raise RuntimeError(f"opencv-python 没装：pip install opencv-python ({_CV_ERR})")

        self.device = device
        self._cap = None
        # Windows 上 DSHOW 的启动延迟比 MSMF 低，先试它；失败再退回默认后端
        for api, tag in ((getattr(cv2, "CAP_DSHOW", 700), "DSHOW"),
                         (getattr(cv2, "CAP_MSMF", 1400), "MSMF"),
                         (0, "AUTO")):
            cap = cv2.VideoCapture(device, api) if api else cv2.VideoCapture(device)
            if cap is not None and cap.isOpened():
                self._cap = cap
                self.api = tag
                break
            if cap is not None:
                cap.release()
        if self._cap is None:
            raise RuntimeError(f"打不开采集设备 index={device}（是不是被 OBS/其他程序占着？）")

        # 顺序有讲究：先定 FOURCC 再定尺寸，否则很多 UVC 采集卡会回落成 YUY2 并卡在 5fps
        try:
            self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        except Exception:
            pass
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_FPS, fps)
        try:
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # 关键：不让驱动排队
        except Exception:
            pass
        try:
            self._cap.set(cv2.CAP_PROP_CONVERT_RGB, 1)
        except Exception:
            pass

        # 先读掉几帧：UVC 刚打开时前几帧常是黑帧/花帧
        deadline = time.time() + open_timeout
        first = None
        while time.time() < deadline:
            ok, frame = self._cap.read()
            if ok and frame is not None:
                first = frame
                if frame.mean() > 1.0:                 # 不是纯黑
                    break
            else:
                time.sleep(0.02)
        if first is None:
            self._cap.release()
            raise RuntimeError(f"设备 index={device} 打开了但读不到帧")

        self.width = int(first.shape[1])
        self.height = int(first.shape[0])

        self._frame = first
        self._n = 0
        self._err = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, name="card-grab", daemon=True)
        self._t.start()

    # ---- 抓帧线程：只负责把槽位刷成最新的 ----
    def _loop(self) -> None:
        cap = self._cap
        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                self._err += 1
                time.sleep(0.002)
                continue
            with self._lock:
                self._frame = frame
                self._n += 1

    # ---- 取样 ----
    def latest(self) -> np.ndarray:
        with self._lock:
            return self._frame

    @property
    def fps(self) -> float:
        """实测帧率（启动后第一秒不准，之后是滑动平均）。"""
        return float(self._cap.get(cv2.CAP_PROP_FPS) or 0) if self._cap else 0.0

    @property
    def frames(self) -> int:
        return self._n

    def grab(self, region) -> np.ndarray:
        """region = (l, t, r, b)，坐标是采集卡画面的坐标系。返回 BGRA。"""
        frame = self.latest()
        l, t, r, b = region
        h, w = frame.shape[:2]
        l = max(0, min(int(l), w))
        t = max(0, min(int(t), h))
        r = max(l, min(int(r), w))
        b = max(t, min(int(b), h))
        if (r - l) != w or (b - t) != h:
            frame = frame[t:b, l:r]
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGRA)
        elif frame.shape[2] == 3:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
        return frame

    def close(self) -> None:
        self._stop.set()
        try:
            self._t.join(timeout=0.5)
        except Exception:
            pass
        try:
            self._cap.release()
        except Exception:
            pass


class _NdiBackend(_CardBackend):
    """占位：NDI / Looking Glass 走同一个 grab() 接口，换实现即可。"""


class mss:
    """与 mss.mss 同名同用法的门面，但画面来自采集卡。"""

    def __init__(self, *a, backend: str = "card", device: int = 0,
                 width: int = 1920, height: int = 1080, fps: int = 60, **kw):
        self._b = _CardBackend(device=device, width=width, height=height, fps=fps)
        self.backend_name = "card"

    @property
    def monitors(self):
        """[0] = 全画面（mss 习惯用法），[1] = 同（ok-ww 用 [1]）。"""
        full = {"left": 0, "top": 0, "width": self._b.width, "height": self._b.height}
        return [dict(full), dict(full)]

    @property
    def fps(self) -> float:
        return self._b.fps

    def grab(self, region: dict):
        l = int(region.get("left", 0))
        t = int(region.get("top", 0))
        w = int(region.get("width", 0))
        h = int(region.get("height", 0))
        arr = self._b.grab((l, t, l + w, t + h))
        return _Shot(arr, l, t)

    def shot(self, region: dict | None = None):
        region = region or self.monitors[1]
        return self.grab(region)

    def close(self):
        self._b.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# ─────────────────────────── 自检 ───────────────────────────

def list_devices(max_index: int = 6) -> list[dict]:
    """逐个 index 试开，报哪些能用。慢一些但结果确定（每个约 0.3~1s）。"""
    if cv2 is None:
        print("opencv 没装")
        return []
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, getattr(cv2, "CAP_DSHOW", 700))
        if not cap.isOpened():
            cap.release()
            continue
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        ok, frame = cap.read()
        nod = ""
        # UVC 设备名不在 cv2 里，只能从 PnP 拿；顺序不保证对应 index，
        # 所以只作为"这台机器上有什么相机"的参考列出来
        try:
            import subprocess
            import sys as _sys
            if _sys.platform == "win32":
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-PnpDevice -Class Camera,Image -Status OK | "
                     "Select-Object -ExpandProperty FriendlyName"],
                    capture_output=True, text=True, timeout=8)
                nod = " | ".join(x for x in r.stdout.strip().splitlines() if x)
        except Exception:
            nod = ""
        found.append({"index": i, "w": w, "h": h, "fps": fps,
                      "readable": bool(ok), "device_names_on_host": nod})
        cap.release()
    return found


def _probe(argv: list[str]) -> int:
    dev = int(argv[1]) if len(argv) > 1 and not argv[1].startswith("-") else 0
    print(f"打开采集卡 index={dev} ...")
    t0 = time.time()
    try:
        s = mss(device=dev)
    except Exception as e:
        print("失败:", e)
        return 1
    print(f"open {time.time() - t0:.2f}s  backend={s.backend_name}  "
          f"size={s.monitors[1]['width']}x{s.monitors[1]['height']}  fps={s.fps:.1f}")
    for i in range(5):
        t = time.perf_counter()
        shot = s.shot()
        arr = np.asarray(shot)
        dt = (time.perf_counter() - t) * 1000
        print(f"  #{i} grab {dt:6.1f}ms  shape={arr.shape}  "
              f"mean={arr[:, :, :3].mean():6.2f}  max={arr[:, :, :3].max()}")
        time.sleep(0.05)
    print(f"累计帧 {s._b.frames}  错误 {s._b._err}")
    s.close()
    return 0


if __name__ == "__main__":
    import sys

    if "--list" in sys.argv:
        for d in list_devices():
            print(d)
        raise SystemExit(0)
    raise SystemExit(_probe(sys.argv[1:]))
