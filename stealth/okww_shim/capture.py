"""
capture.py — 采集层替换（mss / BitBlt -> DXGI Desktop Duplication）

为什么换：
  mss 走 GDI BitBlt。BitBlt 每帧都要拿目标窗口的 DC，对全屏/独占渲染
  的客户端要么拿黑图，要么触发一次额外的 DWM 合成；而且在带保护的游戏
  外面频繁 BitBlt 是行为异常信号（正常玩家不会每秒 30 次截图）。
  DXGI Desktop Duplication 是 OBS / 录屏软件用的同一套 API，属于
  "系统里本来就该有的东西"，痕迹中性。

对外提供与 mss 兼容的 mss() 对象：
    with mss() as sct:
        shot = sct.grab({"left":0,"top":0,"width":1920,"height":1080})
        arr = np.array(shot)       # BGRA, HxWx4
"""
from __future__ import annotations

import ctypes
import numpy as np

_RECT = ctypes.wintypes.RECT if hasattr(ctypes, "wintypes") else None


class _Shot:
    """mss.screenshot 兼容壳：np.array(shot) 得到 BGRA。"""
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


class _DxgiBackend:
    def __init__(self, output_idx: int = 0, device_idx: int = 0):
        import dxcam
        self._cam = dxcam.create(output_idx=output_idx, device_idx=device_idx,
                                 output_color="BGRA")
        if self._cam is None:
            raise RuntimeError("dxcam.create 返回 None")
        self._last = None

    def grab(self, region):
        l, t, r, b = region
        for _ in range(3):
            img = self._cam.grab(region=(l, t, r, b))
            if img is not None:
                self._last = img
                return img
        return self._last

    def close(self):
        try:
            self._cam.release()
        except Exception:
            pass


class _MssBackend:
    """回退到真正的 mss 包。

    注意：shim 已经把 sys.modules['mss'] 指向本模块，所以这里必须绕开
    sys.modules 直接按 spec 加载真实包，否则会 import 到自己、无限递归。
    """

    def __init__(self):
        import importlib.util
        import sys
        saved = sys.modules.pop("mss", None)
        try:
            spec = importlib.util.find_spec("mss")
            if spec is None:
                raise ImportError("mss 包不存在")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            self._s = mod.mss()
        finally:
            if saved is not None:
                sys.modules["mss"] = saved

    def grab(self, region):
        l, t, r, b = region
        shot = self._s.grab({"left": l, "top": t, "width": r - l, "height": b - t})
        return np.asarray(shot, dtype=np.uint8)

    def close(self):
        try:
            self._s.close()
        except Exception:
            pass


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class _GdiBackend:
    """最后兜底：GDI BitBlt。全屏独占客户端会拿黑图，只在前两个后端都不可用时用。"""

    def __init__(self):
        self._u = ctypes.windll.user32
        self._g = ctypes.windll.gdi32
        # 64 位下不声明 restype，句柄会被截成 int32 —— BitBlt 静默失败、整张图全黑
        self._u.GetDC.restype = ctypes.c_void_p
        self._u.GetDC.argtypes = [ctypes.c_void_p]
        self._u.ReleaseDC.restype = ctypes.c_int
        self._u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._g.CreateCompatibleDC.restype = ctypes.c_void_p
        self._g.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        self._g.CreateCompatibleBitmap.restype = ctypes.c_void_p
        self._g.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        self._g.SelectObject.restype = ctypes.c_void_p
        self._g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._g.BitBlt.restype = ctypes.c_int
        self._g.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_uint32]
        # 第 6 参必须是 POINTER(_BIH)：写成 c_void_p 时 ctypes 不接受 byref()，
        # 会静默传 NULL，GetDIBits 返回 0，整张图全黑
        self._g.GetDIBits.restype = ctypes.c_int
        self._g.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                      ctypes.c_uint, ctypes.c_void_p,
                                      ctypes.POINTER(_BIH), ctypes.c_uint]
        self._g.DeleteObject.argtypes = [ctypes.c_void_p]
        self._g.DeleteDC.argtypes = [ctypes.c_void_p]

        # 复用 DC/位图：每秒新建几十个 DC 本身就是异常行为特征
        self._hdc = self._u.GetDC(None)
        self._mem = self._g.CreateCompatibleDC(self._hdc)
        self._bmp = None
        self._size = (0, 0)
        self._buf = None
        self._bi = _BIH()
        self._bi.biSize = ctypes.sizeof(_BIH)
        self._bi.biPlanes = 1
        self._bi.biBitCount = 32
        self._bi.biCompression = 0

    def _ensure(self, w: int, h: int) -> None:
        if self._size == (w, h) and self._bmp:
            return
        if self._bmp:
            self._g.DeleteObject(self._bmp)
        self._bmp = self._g.CreateCompatibleBitmap(self._hdc, w, h)  # 必须是屏幕 DC，不是内存 DC
        self._g.SelectObject(self._mem, self._bmp)
        self._size = (w, h)
        self._bi.biWidth = w
        self._bi.biHeight = -h          # top-down，行序与 OpenCV / mss 一致
        self._buf = ctypes.create_string_buffer(w * h * 4)

    def grab(self, region):
        l, t, r, b = region
        w, h = r - l, b - t
        self._ensure(w, h)
        if not self._g.BitBlt(self._mem, 0, 0, w, h, self._hdc, l, t, 0x00CC0020):
            raise RuntimeError("BitBlt 失败")
        if not self._g.GetDIBits(self._mem, self._bmp, 0, h, self._buf,
                                 ctypes.byref(self._bi), 0):
            raise RuntimeError("GetDIBits 失败")
        return np.frombuffer(self._buf, dtype=np.uint8).reshape(h, w, 4).copy()

    def close(self):
        try:
            if self._bmp:
                self._g.DeleteObject(self._bmp)
            if self._mem:
                self._g.DeleteDC(self._mem)
            if self._hdc:
                self._u.ReleaseDC(None, self._hdc)
        except Exception:
            pass
        self._bmp = self._mem = self._hdc = None


class mss:
    """与 mss.mss 同名同用法的门面。"""

    def __init__(self, *a, backend: str = "auto", **kw):
        self.backend_name = ""
        errors = []
        order = [backend] if backend != "auto" else ["dxgi", "mss", "gdi"]
        for name in order:
            try:
                if name == "dxgi":
                    self._b = _DxgiBackend()
                elif name == "mss":
                    self._b = _MssBackend()
                else:
                    self._b = _GdiBackend()
                self.backend_name = name
                break
            except Exception as e:
                errors.append(f"{name}: {e}")
        else:
            raise RuntimeError("所有采集后端都失败: " + " | ".join(errors))

    # mss 的 monitors 语义
    @property
    def monitors(self):
        u = ctypes.windll.user32
        try:
            u.SetProcessDPIAware()
        except Exception:
            pass
        w = u.GetSystemMetrics(0)
        h = u.GetSystemMetrics(1)
        return [{"left": 0, "top": 0, "width": w, "height": h}, 
                {"left": 0, "top": 0, "width": w, "height": h}]

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


def create(region: dict | None = None, backend: str = "auto"):
    return mss(backend=backend)


if __name__ == "__main__":
    s = mss()
    print("backend =", s.backend_name)
    shot = s.grab(s.monitors[1])
    print("size =", shot.size, "mean =", float(np.asarray(shot)[:, :, :3].mean()))
    s.close()
