"""PYTHONPATH 注入点。

Python 启动时会自动 import sitecustomize（如果 sys.path 上有）。把本
目录加进 PYTHONPATH，ok-ww 在被拉起来的那一刻就带上补丁，一个字节都
不用改它的安装目录。

关掉：set OKWW_FG=0
"""

import os
import sys

if os.environ.get("OKWW_FG", "1") not in ("0", "false", "False", ""):
    try:
        import okww_fg

        _res = okww_fg.install(
            sink=os.environ.get("OKWW_FG_BACKEND") or None,
            stealth_dir=os.environ.get("OKWW_STEALTH_DIR") or None,
            foreground=os.environ.get("OKWW_FG_FOREGROUND", "1") not in ("0", "false", "False"),
            quiet=os.environ.get("OKWW_FG_VERBOSE", "0") in ("0", "", "false"),
        )
        if os.environ.get("OKWW_FG_VERBOSE") not in (None, "", "0"):
            print(f"[sitecustomize] okww_fg -> {_res}", file=sys.stderr)
        os.environ["OKWW_FG_STATUS"] = repr(_res)
    except Exception as exc:  # 任何异常都不能拦住 ok-ww 启动
        os.environ["OKWW_FG_STATUS"] = f"error: {exc!r}"
        if os.environ.get("OKWW_FG_VERBOSE") not in (None, "", "0"):
            print(f"[sitecustomize] okww_fg failed: {exc!r}", file=sys.stderr)
