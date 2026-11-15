"""Parse PE import tables + strings of ACE components to see what they can actually do."""
import struct, sys, re, os, json

INTEREST = re.compile(
    r"(OpenProcess|ReadProcessMemory|WriteProcessMemory|EnumProcesses|EnumProcessModules|"
    r"VirtualQueryEx|VirtualProtectEx|CreateToolhelp32Snapshot|Process32|Module32|"
    r"EnumWindows|GetWindowText|GetClassName|GetForegroundWindow|GetWindowThreadProcessId|"
    r"SetWindowsHookEx|GetAsyncKeyState|GetKeyboardState|MapVirtualKey|SendInput|"
    r"NtUserSendInput|NtUserPostMessage|NtUserSetWindowsHookEx|NtUserGetAsyncKeyState|"
    r"BitBlt|GetDC|PrintWindow|CreateCompatibleBitmap|GetDIBits|DwmGetWindowAttribute|"
    r"NtQuerySystemInformation|NtQueryInformationProcess|NtOpenProcess|NtSuspendProcess|"
    r"CreateRemoteThread|NtCreateThreadEx|QueueUserAPC|SetThreadContext|"
    r"MmCopyVirtualMemory|KeStackAttachProcess|KeUnstackDetachProcess|PsLookupProcessByProcessId|"
    r"ObOpenObjectByPointer|ZwOpenProcess|ZwReadVirtualMemory|PsSetLoadImageNotifyRoutine|"
    r"PsSetCreateProcessNotifyRoutine|PsSetCreateThreadNotifyRoutine|PsGetProcessImageFileName|"
    r"CmRegisterCallback|CmRegisterCallbackEx|FltRegisterFilter|ObRegisterCallbacks|"
    r"PsRemoveLoadImageNotifyRoutine|ZwQuerySystemInformation|SeAccessCheck|"
    r"IoCreateDevice|IRP_MJ_|NtDeviceIoControlFile|DeviceIoControl)", re.I)

STR_KEYS = re.compile(
    rb"(?i)(ok[-_ ]?ww|ok[-_ ]?script|okscript|wuthering|kuro|"
    rb"bettercmd|cheat[-_ ]?engine|frida|interception|pydirectinput|pyautogui|"
    rb"ahk|autohotkey|python\.exe|onnxruntime|opencv|auto[-_ ]?combat|macro|"
    rb"SGuard|ACE-|anticheatexpert)" b"|" + "鸣潮".encode("gbk"))


def u16(b, o): return struct.unpack_from("<H", b, o)[0]
def u32(b, o): return struct.unpack_from("<I", b, o)[0]
def u64(b, o): return struct.unpack_from("<Q", b, o)[0]


def parse(path):
    b = open(path, "rb").read()
    if b[:2] != b"MZ":
        return None
    pe = u32(b, 0x3C)
    if b[pe:pe+4] != b"PE\0\0":
        return None
    nsec = u16(b, pe + 6)
    optsz = u16(b, pe + 20)
    opt = pe + 24
    magic = u16(b, opt)
    is64 = magic == 0x20B
    dd = opt + (112 if is64 else 96)
    imgbase = u64(b, opt + 24) if is64 else u32(b, opt + 28)
    secs = []
    so = opt + optsz
    for i in range(nsec):
        s = so + i * 40
        name = b[s:s+8].rstrip(b"\0").decode("latin1")
        vsz = u32(b, s + 8); va = u32(b, s + 12)
        rawsz = u32(b, s + 16); raw = u32(b, s + 20)
        secs.append((name, va, max(vsz, rawsz), raw, rawsz))

    def r2o(rva):
        for name, va, sz, raw, rawsz in secs:
            if va <= rva < va + sz:
                off = rva - va
                if off >= rawsz:
                    return None
                return raw + off
        return None

    def cstr(rva):
        o = r2o(rva)
        if o is None:
            return None
        e = b.find(b"\0", o)
        if e < 0 or e - o > 256:
            return None
        try:
            s = b[o:e].decode("latin1")
        except Exception:
            return None
        if all(32 <= ord(c) < 127 for c in s):
            return s
        return None

    def imports(dd_index, label):
        if dd_index * 8 + 8 > optsz + 24:
            return []
        rva = u32(b, dd + dd_index * 8)
        if not rva:
            return []
        out = []
        d = r2o(rva)
        if d is None:
            return []
        while True:
            if d + 20 > len(b):
                break
            ilt = u32(b, d); ts = u32(b, d + 4); fc = u32(b, d + 8)
            namerva = u32(b, d + 12); iat = u32(b, d + 16)
            if not (ilt or namerva or iat):
                break
            dll = cstr(namerva) or "?"
            thunk = ilt or iat
            names = []
            t = r2o(thunk) if thunk else None
            if t is not None:
                step = 8 if is64 else 4
                while t + step <= len(b):
                    v = u64(b, t) if is64 else u32(b, t)
                    if not v:
                        break
                    if not (v & (1 << 63 if is64 else 1 << 31)):
                        nm = cstr((v & 0x7FFFFFFF) if not is64 else (v & 0x7FFFFFFF))
                        if nm:
                            names.append(nm)
                    t += step
            out.append((label, dll, names))
            d += 20
        return out

    imps = imports(1, "import") + imports(13, "delay")
    # strings
    strs = {}
    for m in re.finditer(rb"[\x20-\x7e]{5,80}", b):
        s = m.group()
        if STR_KEYS.search(s):
            for k in STR_KEYS.findall(s):
                kk = k.decode("latin1", "replace").lower()
                strs.setdefault(kk, set()).add(s.decode("latin1", "replace"))
    return {"path": path, "is64": is64, "size": len(b),
            "imports": imps, "strings": strs, "raw": b}


targets = [
    r"C:\Program Files\AntiCheatExpert\ACE-BASE.sys",
    r"C:\Program Files\AntiCheatExpert\ACE-CORE206010031.sys",
    r"C:\Program Files\AntiCheatExpert\ACE-CORE106010031.sys",
    r"C:\Program Files\AntiCheatExpert\ACE-CORE206010014.sys",
    r"C:\Program Files\AntiCheatExpert\ACE-Service64.exe",
    r"C:\Program Files\AntiCheatExpert\SGuard\x64\SGuard64.exe",
    r"C:\Program Files\AntiCheatExpert\SGuard\x64\SGuardSvc64.exe",
    r"C:\Program Files\AntiCheatExpert\SGuard\x64\ACE-Safe.dll",
    r"C:\Program Files\AntiCheatExpert\SGuard\x64\ACE-Trace.dll",
    r"C:\Program Files\AntiCheatExpert\SGuard\x64\Plugins\ACE-CSI64.dll",
    r"C:\Program Files\AntiCheatExpert\SGuard\x64\Plugins\ACE-DFS64.dll",
]

for t in targets:
    if not os.path.exists(t):
        print(f"\n=== {os.path.basename(t)} : MISSING ===")
        continue
    r = parse(t)
    if not r:
        print(f"\n=== {os.path.basename(t)} : not PE ===")
        continue
    print(f"\n=== {os.path.basename(t)}  ({r['size']} bytes, {'x64' if r['is64'] else 'x86'}) ===")
    hits = []
    for label, dll, names in r["imports"]:
        sel = [n for n in names if INTEREST.search(n)]
        if sel:
            hits.append((label, dll, sel))
    if hits:
        for label, dll, sel in hits:
            print(f"  [{label}] {dll}: {', '.join(sorted(set(sel)))}")
    else:
        print("  (no interesting imported APIs — likely resolves dynamically)")
    # dll list only
    dlls = sorted({dll for _, dll, _ in r["imports"]})
    print(f"  dlls: {', '.join(dlls)}")
    for k, v in sorted(r["strings"].items()):
        sample = sorted(v)[:6]
        print(f"  str[{k}] x{len(v)}: {' | '.join(sample)}")
