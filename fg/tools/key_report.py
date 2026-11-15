"""逐个试键：F1 / F2 / Esc / Q / 鼠标中键。

必须在管理员权限下跑（UIPI：低权限进程的 SendInput 会被系统静默丢弃，
游戏 Client-Win64-Shipping.exe 是提权进程）。
结果落盘 _keys_out.txt。
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time

sys.path.insert(0, r"D:\ai\dfy\okww-fg")

OUT = r"D:\ai\dfy\okww-fg\_keys_out.txt"
lines = []


def log(s=""):
    lines.append(str(s))


try:
    from okww_fg import foreground as fg
    from PIL import ImageGrab
    import numpy as np
except Exception as e:
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(f"import failed: {type(e).__name__}: {e}\n")
    raise SystemExit(1)

u = fg.user32
KEYUP = 0x0002
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040


def find_game():
    """枚举可见顶层窗口，找鸣潮。"""
    found = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    u.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
    u.IsWindowVisible.argtypes = [wt.HWND]

    def cb(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        title = fg.window_title(hwnd)
        cls = fg.window_class(hwnd)
        if "鸣潮" in title or cls == "UnrealWindow":
            r = wt.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            found.append({"hwnd": int(hwnd), "title": title, "class": cls,
                          "w": r.right - r.left, "h": r.bottom - r.top})
        return True

    u.EnumWindows(WNDENUMPROC(cb), 0)
    found.sort(key=lambda x: -(x["w"] * x["h"]))
    return found[0] if found else None


def shot():
    return np.asarray(ImageGrab.grab(all_screens=False).convert("L"), np.uint8)


def diff(a, b):
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return float(d.mean()), float((d > 24).mean() * 100)


def ensure_fg(hwnd):
    if fg.foreground_hwnd() == hwnd:
        return True
    r = fg.force_foreground(hwnd)
    time.sleep(0.4)
    return fg.foreground_hwnd() == hwnd


def step(name, act):
    ok = ensure_fg(GAME)
    if not ok:
        log(f"  {name:<14} 切不到前台，跳过")
        return None
    time.sleep(0.45)
    a = shot()
    act()
    time.sleep(0.85)
    b = shot()
    m, pct = diff(a, b)
    hit = "有反应" if pct > 2.0 else "没反应"
    log(f"  {name:<14} 差异 mean={m:6.2f}  变化 {pct:6.2f}%  -> {hit}")
    return pct


def tap_key(vk, hold=0.06):
    def _():
        u.keybd_event(vk, 0, 0, None)
        time.sleep(hold)
        u.keybd_event(vk, 0, KEYUP, None)
    return _


def tap_middle():
    u.mouse_event(MOUSEEVENTF_MIDDLEDOWN, 0, 0, 0, None)
    time.sleep(0.06)
    u.mouse_event(MOUSEEVENTF_MIDDLEUP, 0, 0, 0, None)


# 完整性自检
import os
adv, k32 = ctypes.windll.advapi32, ctypes.windll.kernel32
h = k32.OpenProcess(0x1000, False, os.getpid())
tok = wt.HANDLE()
k32.OpenProcessToken(h, 0x0008, ctypes.byref(tok))
buf, ret = wt.DWORD(0), wt.DWORD(0)
adv.GetTokenInformation(tok, 20, ctypes.byref(buf), ctypes.sizeof(buf), ctypes.byref(ret))
log(f"完整性: {'提权(管理员)' if buf.value else '普通权限（输入会失败）'}")

g = find_game()
if not g:
    log("没找到游戏窗口")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    raise SystemExit(1)

GAME = g["hwnd"]
log(f"游戏窗口: hwnd={GAME}  class={g['class']}  title={g['title']!r}  {g['w']}x{g['h']}")
log(f"当前前台: {fg.foreground_hwnd()}")
log("")

# 先切过去并留 1 秒稳定
ensure_fg(GAME)
time.sleep(1.0)
log(f"切过去后前台 = {fg.foreground_hwnd()}")
log("")
log("=== 逐个试键（每个键前自动确保游戏在前台）===")
step("F1", tap_key(0x70))
step("F1 再按(收回)", tap_key(0x70))
step("F2", tap_key(0x71))
step("F2 再按(收回)", tap_key(0x71))
step("Q", tap_key(0x51))
step("鼠标中键", tap_middle)
step("Esc", tap_key(0x1B))

log("")
log("说明：变化率 >2% 视为「游戏确实收到了这个键」。")
log("      F1/F2 若开了面板，紧随的「再按」应该把它收回，两行都有反应即证明是开关型。")
log("      Q 是声骸技能，角色没锁定目标时也可能只有小幅动作。")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
