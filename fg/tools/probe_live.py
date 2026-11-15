"""真机全链路体检（不动游戏）：找窗口 → 切前台 → 截图 → 读像素 → 还原。

不发任何按键/点击，只证明「能切过去 + 能抓到画面」。

跑法（用 ok-ww 自带的 python）：
    & "C:\\Users\\lzk11\\AppData\\Local\\ok-ww\\data\\apps\\ok-ww\\python\\python.exe" tools\\probe_live.py
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from okww_fg import foreground as fg  # noqa: E402

user32 = fg.user32
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_game() -> dict | None:
    hits = []

    @WNDENUMPROC
    def cb(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            cls = fg.window_class(hwnd) or ""
            title = fg.window_title(hwnd) or ""
            if "unrealwindow" in cls.lower() or "wuthering" in title.lower() or "鸣潮" in title:
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                hits.append({
                    "hwnd": int(hwnd), "title": title, "class": cls,
                    "rect": (r.left, r.top, r.right - r.left, r.bottom - r.top),
                })
        except Exception:
            pass
        return True

    user32.EnumWindows(cb, 0)
    return hits[0] if hits else None


def grab(region=None):
    """抓一帧，返回 (width, height, mean, min, max)。"""
    from PIL import ImageGrab
    import numpy as np

    img = ImageGrab.grab(bbox=region, all_screens=False)
    a = np.asarray(img.convert("RGB"), dtype=np.uint8)
    return a.shape[1], a.shape[0], float(a.mean()), int(a.min()), int(a.max())


def main() -> int:
    print("=" * 70)
    print("真机全链路体检")
    print("=" * 70)

    game = find_game()
    if not game:
        print("没找到 UnrealWindow。游戏是不是最小化了？")
        return 2

    print(f"游戏窗口  hwnd={game['hwnd']}  class={game['class']!r}")
    print(f"          标题={game['title']!r}")
    print(f"          矩形={game['rect']}")
    print(f"前台锁    {fg.get_lock_timeout()}")

    before = fg.foreground_hwnd()
    print(f"\n切换前前台 hwnd={before}  {fg.window_title(before)!r}")

    print("\n-- 抓一帧（游戏还没在前台）--")
    try:
        w, h, mean, lo, hi = grab()
        print(f"   截图 {w}x{h}  mean={mean:.1f}  min={lo}  max={hi}")
    except Exception as e:
        print(f"   截图失败：{type(e).__name__}: {e}")

    print("\n-- 切前台 --")
    t0 = time.perf_counter()
    res = fg.force_foreground(game["hwnd"])
    dt = (time.perf_counter() - t0) * 1000
    after = fg.foreground_hwnd()
    print(f"   ok={res.get('ok')}  elapsed={res.get('elapsed')}  steps={res.get('steps')}  外圈={dt:.1f}ms")
    print(f"   after={after}  {fg.window_title(after)!r}")

    switched = res.get("ok") and int(after) == int(game["hwnd"])
    print(f"   -> {'PASS 真切过去了' if switched else 'FAIL 没切过去'}")

    if switched:
        print("\n-- 切过去之后再抓一帧 --")
        time.sleep(0.35)
        try:
            w2, h2, mean2, lo2, hi2 = grab()
            print(f"   截图 {w2}x{h2}  mean={mean2:.1f}  min={lo2}  max={hi2}")
        except Exception as e:
            print(f"   截图失败：{type(e).__name__}: {e}")

        cx, cy = game["rect"][0] + game["rect"][2] // 2, game["rect"][1] + game["rect"][3] // 2
        print(f"\n   游戏中心屏幕坐标 = ({cx}, {cy})，光标现在在 {fg.cursor_pos()}")

    print("\n" + "=" * 70)
    print("没发任何按键/点击。窗口停在游戏上，需要的话 Alt+Tab 切回去。")
    print("=" * 70)
    return 0 if switched else 1


if __name__ == "__main__":
    raise SystemExit(main())
