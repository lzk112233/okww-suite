"""okww-fg — 把 ok-ww 的后台 PostMessage 输入改成真前台 + 真注入。

用法一（注入，不改 ok-ww 任何文件）：
    set PYTHONPATH=D:\\ai\\dfy\\okww-fg
    python -m ok
    sitecustomize.py 会自动 install()

用法二（显式调用）：
    import okww_fg
    okww_fg.install(sink="sendinput", stealth_dir=r"D:\\ai\\dfy\\okww-stealth")

环境变量：
    OKWW_FG=0            关闭注入
    OKWW_FG_BACKEND      sendinput / interception / hid / net_hid
    OKWW_STEALTH_DIR     okww-stealth 目录（提供 human_input 与硬件 HID 后端）
    OKWW_FG_VERBOSE=1    打印安装细节
"""

from .interaction import install, status  # noqa: F401
from . import foreground  # noqa: F401

__all__ = ["install", "status", "foreground"]
__version__ = "1.0.0"
