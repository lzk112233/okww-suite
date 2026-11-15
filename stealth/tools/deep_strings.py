"""Dig strings out of ACE user-mode modules: dynamically-resolved API names,
cheat-tool blacklist entries, Chinese (GBK) strings, mutex/registry keys."""
import re, os, sys, collections

API_WORDS = [
    # process / memory
    "OpenProcess", "ReadProcessMemory", "WriteProcessMemory", "NtOpenProcess",
    "NtReadVirtualMemory", "NtWriteVirtualMemory", "NtQuerySystemInformation",
    "NtQueryInformationProcess", "EnumProcesses", "EnumProcessModules",
    "CreateToolhelp32Snapshot", "Process32First", "Module32First",
    "VirtualQueryEx", "VirtualProtectEx", "VirtualAllocEx", "CreateRemoteThread",
    "NtCreateThreadEx", "QueueUserAPC", "SetThreadContext", "NtSuspendProcess",
    "NtDuplicateObject", "NtQueryObject", "NtQueryVirtualMemory",
    # window / input
    "EnumWindows", "GetWindowText", "GetClassName", "GetForegroundWindow",
    "GetWindowThreadProcessId", "SetWindowsHookEx", "GetAsyncKeyState",
    "GetKeyboardState", "GetKeyState", "MapVirtualKey", "SendInput",
    "NtUserSendInput", "NtUserPostMessage", "NtUserSetWindowsHookEx",
    "NtUserGetAsyncKeyState", "RegisterRawInputDevices", "GetRawInputData",
    "GetRawInputDeviceList", "GetRawInputDeviceInfo", "RIDEV_INPUTSINK",
    # screen
    "BitBlt", "PrintWindow", "GetDIBits", "CreateCompatibleBitmap",
    "DwmGetWindowAttribute", "GetSystemMetrics", "GetDC",
    # driver / device
    "DeviceIoControl", "NtDeviceIoControlFile", "CreateFileW", "NtCreateFile",
    "NtFsControlFile", "LoadDriver", "NtLoadDriver",
    # net
    "WSAConnect", "connect", "WSASend", "HttpOpenRequestW", "WinHttpOpen",
    # anti-debug
    "IsDebuggerPresent", "CheckRemoteDebuggerPresent", "NtQueryDebugFilterState",
    "OutputDebugStringW", "QueryPerformanceCounter", "NtSetInformationThread",
    "NtQueryInformationThread", "GetTickCount64",
]

CHEAT_WORDS = [
    "cheatengine", "cheat engine", "CheatEngine", "x64dbg", "x32dbg", "ollydbg",
    "ida64", "ida.exe", "idaq", "windbg", "ghidra", "frida", "interception",
    "pydirectinput", "pyautogui", "autohotkey", "AutoHotkey", "ahk",
    "bettercmd", "BetterCmd", "simulate", "SimulateInput", "kmbox", "KMBox",
    "arduino", "Arduino", "raspberry", "hid", "HID", "virtual", "VirtualHID",
    "macro", "Macro", "script", "Script", "auto", "Auto", "bot", "Bot",
    "okww", "ok-ww", "okww", "wuther", "Wuthering", "kuro", "Kuro",
    "鎸夐敭绮剧伒", "鑴氭湰", "澶栨寕", "鑷姩", "杩炵偣", "妯℃嫙", "杈呭姪",
    "python", "Python", "pythonw", "pyw",
    "EasyHook", "Detours", "minhook", "MinHook", "hook",
]


def load(path):
    return open(path, "rb").read()


def ascii_strings(b, minlen=4, maxlen=120):
    out = []
    for m in re.finditer(rb"[\x20-\x7e]{%d,%d}" % (minlen, maxlen), b):
        out.append((m.start(), m.group().decode("latin1")))
    return out


def gbk_strings(b):
    """Chinese strings stored as GBK: sequences of double-byte 0xB0-0xF7 0xA1-0xFE."""
    out = []
    i = 0
    n = len(b)
    while i < n - 1:
        c = b[i]
        if 0xB0 <= c <= 0xF7 and 0xA1 <= b[i+1] <= 0xFE:
            start = i
            chunk = bytearray()
            while i < n - 1 and 0xB0 <= b[i] <= 0xF7 and 0xA1 <= b[i+1] <= 0xFE:
                chunk += b[i:i+2]
                i += 2
            if len(chunk) >= 4:
                try:
                    s = chunk.decode("gbk")
                except Exception:
                    continue
                if all("\u4e00" <= ch <= "\u9fff" or ch.isprintable() for ch in s):
                    out.append((start, s))
        else:
            i += 1
    return out


def utf16_strings(b, minlen=4, maxlen=120):
    out = []
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){%d,%d}" % (minlen, maxlen), b):
        out.append((m.start(), m.group().decode("utf-16-le", "replace")))
    return out


def report(path):
    b = load(path)
    name = os.path.basename(path)
    print(f"\n{'='*70}\n=== {name}  ({len(b):,} bytes) ===")

    asc = ascii_strings(b)
    blob = "\n".join(s for _, s in asc)
    low = blob.lower()

    # 1) dynamically resolved API names
    found = [(w, low.count(w.lower())) for w in API_WORDS if w.lower() in low]
    if found:
        print("\n-- API names present as strings (dynamically resolved) --")
        for w, c in sorted(found, key=lambda x: -x[1]):
            print(f"   {w:<32} x{c}")

    # 2) cheat/script tool keywords
    hits = collections.defaultdict(set)
    for off, s in asc:
        sl = s.lower()
        for w in CHEAT_WORDS:
            if w.lower() in sl and len(w) >= 3:
                hits[w.lower()].add(s.strip())
    if hits:
        print("\n-- tool/keyword strings --")
        for w in sorted(hits, key=lambda k: -len(hits[k])):
            samples = sorted(hits[w])[:4]
            print(f"   [{w}] x{len(hits[w])}: " + " | ".join(x[:70] for x in samples))

    # 3) Chinese strings
    cn = gbk_strings(b)
    seen = set()
    uniq = []
    for off, s in cn:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    if uniq:
        print(f"\n-- GBK Chinese strings ({len(uniq)} unique) --")
        for s in uniq[:60]:
            print("   " + s[:60])

    # 4) utf16
    u16 = collections.Counter(s for _, s in utf16_strings(b))
    interesting = [s for s in u16 if re.search(r"[\\/:.]|ACE|SGuard|Anti|Guard|\.exe|\.dll|\.sys", s)]
    if interesting:
        print(f"\n-- UTF-16 strings ({len(u16)} unique, showing paths/names) --")
        for s in interesting[:40]:
            print(f"   {s[:90]}")


for t in [
    r"C:\Program Files\AntiCheatExpert\SGuard\x64\SGuard64.exe",
    r"C:\Program Files\AntiCheatExpert\SGuard\x64\ACE-Safe.dll",
    r"C:\Program Files\AntiCheatExpert\ACE-Service64.exe",
]:
    if os.path.exists(t):
        report(t)
    else:
        print(f"\nMISSING {t}")

