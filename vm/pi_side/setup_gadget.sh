#!/bin/bash
# setup_gadget.sh — 把树莓派配置成 USB 复合 HID 设备（键盘 + 鼠标）
#
# 硬件前提
#   ✅ Pi Zero / Zero 2 W —— 用标着 "USB" 的那个 micro-USB 口
#   ✅ Pi 4 / Pi 400      —— 用 USB-C 口（dr_mode=peripheral）
#   ✅ Pi 3               —— 用 USB OTG 口
#   ❌ Pi 5               —— USB-C 只有供电，没有 device 模式
#
# 用法
#   bash setup_gadget.sh --check     # 只体检，不改任何东西
#   sudo bash setup_gadget.sh        # 配置并立刻建立 /dev/hidg0 /dev/hidg1
#
# 本脚本可重复执行（幂等）。

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VID="${VID:-0x046D}"        # 默认填罗技；两台机器建议改成不一样的
PID="${PID:-0xC31C}"
MANUF="${MANUF:-Logitech}"
PRODUCT="${PRODUCT:-USB Receiver}"
GADGET="okww"
UDC_NAME=""

CHECK_ONLY=0
if [[ "${1:-}" == "--check" ]]; then
    CHECK_ONLY=1
fi

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# ── 1. 体检 ──
say "体检"

MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
say "机型: $MODEL"
if grep -qi "Raspberry Pi 5" <<<"$MODEL"; then
    warn "Pi 5 的 USB-C 不支持 device 模式，这个方案在 Pi 5 上用不了"
    warn "换 Pi Zero 2 W / Pi 4，或者用 RP2040 板子走 serial 模式"
fi

BOOTCFG=""
for f in /boot/firmware/config.txt /boot/config.txt; do
    if [[ -f $f ]]; then
        BOOTCFG="$f"
        break
    fi
done
[[ -n $BOOTCFG ]] || die "找不到 config.txt"
CMDLINE="${BOOTCFG%/*}/cmdline.txt"
say "启动配置: $BOOTCFG"

have_overlay=$(grep -c '^dtoverlay=dwc2' "$BOOTCFG" || true)
have_module=$(grep -c 'modules-load=dwc2' "$CMDLINE" 2>/dev/null || true)
say "dtoverlay=dwc2 : $([[ $have_overlay -gt 0 ]] && echo 已有 || echo 缺失)"
say "modules-load   : $([[ $have_module -gt 0 ]] && echo 已有 || echo 缺失)"

if [[ ! -e /sys/class/udc ]] || [[ -z "$(ls /sys/class/udc 2>/dev/null)" ]]; then
    warn "没有 UDC 控制器（dwc2 没加载？改完启动项要重启）"
fi

if [[ $CHECK_ONLY -eq 1 ]]; then
    say "体检结束（--check，没有改动）"
    exit 0
fi

[[ $EUID -eq 0 ]] || die "要用 sudo 跑"

# ── 2. 启动项 ──
if [[ $have_overlay -eq 0 ]]; then
    say "往 $BOOTCFG 追加 dtoverlay=dwc2"
    printf '\n# ok-ww HID bridge\ndtoverlay=dwc2,dr_mode=peripheral\n' >> "$BOOTCFG"
    NEED_REBOOT=1
fi
if [[ $have_module -eq 0 ]]; then
    say "往 $CMDLINE 注入 modules-load=dwc2,libcomposite"
    cp "$CMDLINE" "${CMDLINE}.bak.$$"
    # cmdline 是单行，root= 之后插
    sed -i 's/\(root=[^ ]*\)/\1 modules-load=dwc2,libcomposite/' "$CMDLINE"
    NEED_REBOOT=1
fi

modprobe libcomposite 2>/dev/null || warn "modprobe libcomposite 失败，重启后再试"

# ── 3. configfs ──
say "建立 gadget"
if command -v mountpoint >/dev/null 2>&1; then
    mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config
else
    grep -q ' /sys/kernel/config ' /proc/mounts || mount -t configfs none /sys/kernel/config
fi
CFG=/sys/kernel/config/usb_gadget/$GADGET
if [[ -d $CFG ]]; then
    say "清理旧的 $GADGET"
    echo "" > "$CFG/UDC" 2>/dev/null || true
    rm -rf "$CFG"
fi
mkdir -p "$CFG"
cd "$CFG"

echo "$VID" > idVendor
echo "$PID" > idProduct
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB

mkdir -p strings/0x409
echo "0123456789" > strings/0x409/serialnumber
echo "$MANUF"    > strings/0x409/manufacturer
echo "$PRODUCT"  > strings/0x409/product

mkdir -p configs/c.1
echo 250 > configs/c.1/MaxPower
mkdir -p configs/c.1/strings/0x409
echo "HID Composite" > configs/c.1/strings/0x409/configuration

# ── 4. 报告描述符（从 hidg.py 生成，避免手抄十六进制出错）──
say "生成报告描述符"
python3 -c "import sys; sys.path.insert(0,'$HERE'); import hidg; sys.stdout.buffer.write(hidg.KB_REPORT_DESC)" > /tmp/okww_kb.desc
python3 -c "import sys; sys.path.insert(0,'$HERE'); import hidg; sys.stdout.buffer.write(hidg.MS_REPORT_DESC)" > /tmp/okww_ms.desc
say "键盘描述符 $(stat -c%s /tmp/okww_kb.desc) 字节 / 鼠标 $(stat -c%s /tmp/okww_ms.desc) 字节"

mkdir -p functions/hid.usb0        # 键盘 → /dev/hidg0
echo 1 > functions/hid.usb0/protocol        # 1 = Keyboard
echo 1 > functions/hid.usb0/subclass        # 1 = Boot Interface
echo 8 > functions/hid.usb0/report_length
cat /tmp/okww_kb.desc > functions/hid.usb0/report_desc

mkdir -p functions/hid.usb1        # 鼠标 → /dev/hidg1
echo 2 > functions/hid.usb1/protocol        # 2 = Mouse
echo 1 > functions/hid.usb1/subclass
echo 4 > functions/hid.usb1/report_length
cat /tmp/okww_ms.desc > functions/hid.usb1/report_desc

ln -sf functions/hid.usb0 configs/c.1/
ln -sf functions/hid.usb1 configs/c.1/

# ── 5. 绑定 ──
UDC_NAME="$(ls /sys/class/udc | head -1)"
[[ -n $UDC_NAME ]] || die "没有可用 UDC，重启后再跑"
echo "$UDC_NAME" > UDC
say "已绑定 UDC: $UDC_NAME"

sleep 0.2
chmod 666 /dev/hidg0 /dev/hidg1 2>/dev/null || warn "chmod /dev/hidg* 失败"
say "设备节点:"
ls -l /dev/hidg* 2>/dev/null || die "/dev/hidg* 没出现"

cat <<EOF

完成。接下来：
  1) 把 USB 口接到游戏机上（不是 VM）
  2) 现场试一下：  python3 $HERE/hidg.py
     目标机光标应该右移并点一下
  3) 起服务：      sudo python3 $HERE/bridge_server.py \\
                     --mode gadget --port 8765
  4) VM 里设：     set OKWW_HID_HOST=<树莓派IP>

EOF

if [[ "${NEED_REBOOT:-0}" -eq 1 ]]; then
    warn "改过启动项，建议 sudo reboot 后再跑一次本脚本"
fi
