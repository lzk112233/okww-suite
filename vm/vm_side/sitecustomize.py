"""
sitecustomize.py — 只要 vm_side 在 PYTHONPATH 上，Python 启动时自动装好 VM 桥

    set PYTHONPATH=D:\\ai\\dfy\\okww-vm\\vm_side

想临时关掉：
    set OKWW_VM_SHIM=0
"""
from __future__ import annotations

import os

if os.environ.get("OKWW_VM_SHIM", "1") != "0":
    try:
        from install_vm import install_vm

        _st = install_vm()
        os.environ["OKWW_VM_STATUS"] = str(_st.get("ok"))
        if os.environ.get("OKWW_VM_VERBOSE", "0") == "1":
            print("[okww-vm]", _st)
    except Exception as _e:          # 装不上也不要拦着 ok-ww 启动
        os.environ["OKWW_VM_STATUS"] = f"error: {_e}"
        if os.environ.get("OKWW_VM_VERBOSE", "0") == "1":
            print("[okww-vm] install failed:", _e)
