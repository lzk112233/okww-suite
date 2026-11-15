"""真机体检：找到游戏窗口，看 force_foreground 能不能切过去。

只切前台 + 读窗口信息，不发任何按键/点击。

跑法：
    python tools\\probe_game.py            # 只体检
    python tools\\probe_game.py --switch   # 体检 + 真的切一次前台
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from okww_fg import foreground as fg  # noqa: E402

user32 = fg.user32

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsZoomed.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
try:
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
except AttributeError:
    pass

GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_POPUP = 0x80000000

KEYWORDS = ("wuthering", "鸣潮", "client-win64", "client-win32", "kuro", "game")


def enum_windows(visible_only=True):
    out = []

    @WNDENUMPROC
    def cb(hwnd, _lparam):
        try:
            if visible_only and not user32.IsWindowVisible(hwnd):
                return True
            title = fg.window_title(hwnd) or ""
            cls = fg.window_class(hwnd) or ""
            if not title and not cls:
                return True
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w <= 0 or h <= 0:
                return True
            out.append({
                "hwnd": int(hwnd),
                "title": title,
                "class": cls,
                "rect": (rect.left, rect.top, w, h),
                "zoomed": bool(user32.IsZoomed(hwnd)),
                "iconic": bool(user32.IsIconic(hwnd)),
                "style": int(user32.GetWindowLongW(hwnd, GWL_STYLE)) & 0xFFFFFFFF,
            })
        except Exception:
            pass
        return True

    user32.EnumWindows(cb, 0)
    return out


def score(w: dict) -> int:
    t = (w["title"] or "").lower()
    c = (w["class"] or "").lower()
    s = 0
    if "wuthering" in t:
        s += 100
    if "鸣潮" in w["title"]:
        s += 100
    if "client-win64" in c or "client-win64" in t:
        s += 60
    if "unrealwindow" in c:
        s += 50
    if "kuro" in c or "kuro" in t:
        s += 40
    if any(k in t for k in KEYWORDS) or any(k in c for k in KEYWORDS):
        s += 10
    rw, rh = w["rect"][2], w["rect"][3]
    if rw >= 1200 and rh >= 700:
        s += 20
    return s


def main() -> int:
    want_switch = "--switch" in sys.argv

    print("=" * 74)
    print("环境")
    print("=" * 74)
    print(f"  SPI_GETFOREGROUNDLOCKTIMEOUT = {fg.get_lock_timeout()}")
    cur = fg.foreground_hwnd()
    print(f"  当前前台 hwnd={cur}  title={fg.window_title(cur)!r}  class={fg.window_class(cur)!r}")
    print()

    wins = enum_windows()
    wins.sort(key=score, reverse=True)

    print("=" * 74)
    print(f"可见窗口 {len(wins)} 个，按「像游戏」打分排序，前 12 个")
    print("=" * 74)
    print(f"{'hwnd':<12}{'分':<5}{'尺寸':<14}{'最大化':<7}{'class':<26}title")
    print("-" * 74)
    for w in wins[:12]:
        r = w["rect"]
        print(f"{w['hwnd']:<12}{score(w):<5}{str(r[2]) + 'x' + str(r[3]):<14}"
              f"{str(w['zoomed']):<7}{(w['class'] or '')[:25]:<26}"
              f"{(w['title'] or '')[:34]}")
    print("-" * 74)
    print()

    cands = [w for w in wins if score(w) >= 50]
    if not cands:
        print("没有找到像游戏的窗口。把游戏切到窗口化/无边框再跑一次。")
        return 2

    game = cands[0]
    print(f"选中：hwnd={game['hwnd']}  {game['title']!r}  class={game['class']!r}")
    print(f"  窗口矩形 = {game['rect']}   最大化={game['zoomed']}  最小化={game['iconic']}")
    print(f"  风格 = 0x{game['style']:08X}  "
          f"有标题栏={'是' if game['style'] & WS_CAPTION else '否'}"
          f"  WS_POPUP={'是' if game['style'] & WS_POPUP else '否'}")
    try:
        print(f"  DPI = {user32.GetDpiForWindow(game['hwnd'])}")
    except Exception:
        pass
    print()

    if not want_switch:
        print("只体检。（加 --switch 才会真的切一次前台）")
        return 0

    print("=" * 74)
    print("切前台")
    print("=" * 74)
    before = fg.foreground_hwnd()
    t0 = time.perf_counter()
    res = fg.force_foreground(game["hwnd"])
    dt = (time.perf_counter() - t0) * 1000
    after = fg.foreground_hwnd()
    print(f"  before = {before} ({fg.window_title(before)!r})")
    print(f"  结果   = ok={res.get('ok')}  elapsed={res.get('elapsed')}  "
          f" steps={res.get('steps')}  外圈={dt:.1f}ms")
    print(f"  after  = {after} ({fg.window_title(after)!r})")
    print()
    if res.get("ok") and int(after) == int(game["hwnd"]):
        print("PASS：真切过去了。")
        print("      独占全屏也能切的话，说明你在无边框/窗口化模式，这条链路可用。")
        return 0
    print("FAIL：没切过去。")
    print("  常见原因：①独占全屏（DWM 拦截 SetForegroundWindow）")
    print("            ②游戏进程完整性级别更高（管理员权限的游戏 vs 非管理员 python）")
    print("            ③窗口是 WS_EX_NOACTIVATE / 工具窗口")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
