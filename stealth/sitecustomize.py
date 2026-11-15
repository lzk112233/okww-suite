"""
sitecustomize.py — Python 启动钩子

放在 PYTHONPATH 里，任何 python 进程启动都会自动 import 它。
这样 ok-ww 一行代码都不用改，输入/采集/日志/外连/窗口全部被替换。

安全阀：设 OKWW_SHIM=0 可完全关闭。
"""
import os
import sys

if os.environ.get("OKWW_SHIM", "1") not in ("0", "false", "no"):
    try:
        import okww_shim
        okww_shim.install(
            cloak=os.environ.get("OKWW_CLOAK"),
            block_net=os.environ.get("OKWW_BLOCK_NET", "1") != "0",
            prefer_backend=os.environ.get("OKWW_INPUT_BACKEND"),
        )
    except Exception as exc:  # 注入失败不能拖死宿主进程
        try:
            sys.stderr.write(f"[okww_shim] install failed: {exc}\n")
        except Exception:
            pass
