# okww-vm — 把 ok-ww 关进虚拟机，游戏留在物理机

这套东西解决一个问题：**反作弊在游戏机上找 python 进程**。
方案不是让 python 隐身，而是让它**根本不在那台机器上**。

- 游戏 / ACE 跑在物理机，它看到的是一个真实桌面 + 一个真实 USB 键鼠
- ok-ww 跑在虚拟机，它看到的"屏幕"是采集卡画面，"键鼠"是网络 HID
- 两台机器之间只有一根视频线（采集卡）和一条 TCP（键鼠回程）

**不碰原 ok-ww、不碰 `D:\ai\dfy\okww-stealth`。** 本目录全部是新增文件，
靠运行时 monkey-patch 接管，ok-ww 源码一行不改。

---

## 1. 拓扑

```
┌──────────────── 物理机（装游戏 + ACE） ───────────────┐
│                                                        │
│   游戏画面 ──HDMI──► 采集卡 ──USB──► 虚拟机宿主         │
│                                                        │
│   ACE 看到的世界：                                     │
│     · 进程表里没有 python.exe                          │
│     · 窗口枚举找不到 ok-ww                             │
│     · 文件系统里没有 ok-ww 目录                        │
│     · 键盘鼠标是 USB 口进来的真实设备，无 INJECTED     │
└────────────────────────────────────────────────────────┘
              ▲                              │
              │ USB HID（真设备）             │ HDMI（只出不进）
              │                              ▼
┌──── 树莓派（USB device 模式当键鼠） ────┐  ┌─── 虚拟机 ────┐
│  bridge_server.py  ←── TCP 8765 ────────┼──┤  ok-ww         │
│  /dev/hidg0 键盘 /dev/hidg1 鼠标        │  │  capture_card  │
└─────────────────────────────────────────┘  │  net_hid       │
                                             └────────────────┘
```

注意方向：**视频进 VM，键鼠出 VM**。VM 永远碰不到游戏进程。

---

## 2. 文件

| 文件 | 跑在哪 | 干什么 |
|---|---|---|
| `vm_side/net_hid.py` | 虚拟机 | 把 HID 帧走 TCP 发出去。接口对齐 `human_input.Backend` |
| `vm_side/capture_card.py` | 虚拟机 | 采集卡截图，mss 兼容。后台线程只保留最新帧 |
| `vm_side/install_vm.py` | 虚拟机 | 胶水：装 shim、换 mss、换输入后端、**虚拟光标记账** |
| `vm_side/sitecustomize.py` | 虚拟机 | `PYTHONPATH` 指过来就自动生效 |
| `pi_side/hidg.py` | 树莓派 | 写 `/dev/hidg*`，含报告描述符 |
| `pi_side/bridge_server.py` | 树莓派 | TCP 收帧 → HID（或转发串口给 RP2040） |
| `pi_side/setup_gadget.sh` | 树莓派 | configfs 配 USB 复合 HID |

协议跟上一轮的串口固件**完全一致**（`AA | CMD | LEN | PAYLOAD | XOR`），
所以树莓派两种用法都行：自己出 HID，或者把帧转给已经烧好的 RP2040。

---

## 3. 树莓派侧（桥）

### 3.1 硬件

| 板子 | 能不能当 USB device |
|---|---|
| Pi Zero / Zero 2 W | ✅ 用标着 `USB` 的 micro-USB 口 |
| Pi 4 / Pi 400 | ✅ 用 USB-C 口 |
| Pi 3 | ✅ 用 USB OTG 口 |
| **Pi 5** | ❌ USB-C 只有供电，没有 device 模式 |

没树莓派就用 RP2040 + 副机跑 `--mode serial`。

### 3.2 配置

```bash
# 拷贝 pi_side/ 到树莓派，比如 /home/pi/okww-vm/pi_side
cd ~/okww-vm/pi_side
bash setup_gadget.sh --check      # 先体检
sudo bash setup_gadget.sh         # 配置（改过启动项的话 sudo reboot 再来一次）
```

脚本会：装 `dwc2` / `libcomposite`、建 configfs gadget、从 `hidg.py`
生成报告描述符、绑定 UDC、`chmod 666 /dev/hidg*`。

VID/PID 默认填罗技 `0x046D:0xC31C`。**两台机器（如果你有第二套）改成不一样的**：

```bash
sudo VID=0x046D PID=0xC52B PRODUCT="USB Receiver" bash setup_gadget.sh
```

### 3.3 现场验证

先把 USB 口插到**游戏机**（不是 VM），然后：

```bash
python3 ~/okww-vm/pi_side/hidg.py
```

3 秒后游戏机上的光标应该右移 60px 并点一下左键。
**光标不动就什么都别往下做。**

### 3.4 起服务

```bash
sudo python3 ~/okww-vm/pi_side/bridge_server.py --mode gadget --port 8765
```

输出：

```
桥就绪  0.0.0.0:8765  输出=gadget  （Ctrl-C 退出）
```

想先不接硬件、只看收到什么帧：`--mode dry`。

---

## 4. 虚拟机侧

### 4.1 坐标准备（最容易踩，必须先做）

三条硬要求，少一条坐标就全歪：

1. **虚拟机显示器分辨率 = 游戏机输出分辨率**（都是 1920×1080）
2. **采集卡分辨率 = 游戏机输出分辨率**（采集卡会自动跟随 EDID，
   但 OBS 之类的程序可能抢先把它设成别的）
3. ok-ww 的窗口**不要全屏、不要抢焦点**（它只负责算，不负责点）

第 1 条的原因：ok-ww 有些地方直接读 `GetSystemMetrics`，
那读的是虚拟机的屏幕尺寸，这个我拦不住。

### 4.2 装依赖

```powershell
pip install opencv-python numpy
```

虚拟机里**不需要** pyserial / dxcam / mss（`mss` 会被整个替换掉）。

### 4.3 启动

```powershell
$env:PYTHONPATH = "D:\ai\dfy\okww-vm\vm_side;D:\ai\dfy\okww-stealth"
$env:OKWW_HID_HOST = "192.168.1.50"     # 树莓派 IP
$env:OKWW_HID_PORT = "8765"
$env:OKWW_CARD_DEVICE = "0"             # 采集卡 index
$env:OKWW_VM_VERBOSE = "1"              # 想先看装没装上

python -m ok        # 或在 ok-ww 目录里按它原来的方式启动
```

`sitecustomize.py` 会在 Python 启动时自动跑 `install_vm()`。

先确认状态：

```powershell
cd D:\ai\dfy\okww-vm\vm_side
python install_vm.py
```

正常应该看到：

```json
{
  "ok": true,
  "hid": "net_hid 192.168.1.50:8765",
  "capture": "card#0 1920x1080 @60",
  "cursor": "<cursor 0,0 in 1920x1080 clamp=0>",
  "shim": "{'input': 'sendinput', 'capture': 'patched', 'net': 'blocked', ...}"
}
```

`hid` 那行显示的是网络桥，`capture` 是采集卡 —— 两个都对才算装好。
`shim` 里的 `sendinput` 是历史遗留字段：后端已经被换掉了，那行不用管。

---

## 5. 虚拟光标记账（为什么需要它）

HID 只能发**相对位移**：往右 5px、往下 3px。但 ok-ww 想的是
"点屏幕上 (400, 300) 那个按钮"。

问题：游戏机的光标此刻在哪？虚拟机的 `GetCursorPos` 根本不知道，
它返回的是**虚拟机自己桌面**的光标位置，跟游戏机毫无关系。

所以 `install_vm.py` 做三件事：

1. 自己维护一个绝对坐标 `VirtualCursor`，每次 `rel()` 时同步记账
2. `h.position()` 换成读记账，`h.size()` 换成采集卡尺寸
3. 启动时 `home()` —— 往左上角猛推 2120px 顶死边界，记账归零

之后 `moveTo(400,300)` 算出来的相对位移就是准的。

**记账会漂的场景**：光标撞到屏幕边缘时真实位置被卡住，但记账还在往外跑。
`VirtualCursor.clamped` 会累加，撞多了重新 `home()` 一次即可：

```python
from okww_shim import human_input as hi
from install_vm import _home
_home(hi.get().backend.cursor, hi.get())
```

---

## 6. 实测数据（本机跑的）

| 项目 | 结果 |
|---|---|
| 四种帧编码校验 | MOUSE/KEY/PING/RELEASE 全 OK |
| `net_hid` ↔ `bridge_server --mode dry` | PING OK，114 帧，0 校验错 |
| `home()` 归零 | 20 个 −127 分片，记账落到 (0,0) |
| `moveTo(400,300)` 全链路 | 558 ms，记账落点 (400,305) |
| 采集卡（本机摄像头当测试设备） | 1920×1080@60，`mean=...` 正常 |
| 断连保护 | 连接关闭 / 5 秒无帧都会 `RELEASE` |

落点差 5px 是贝塞尔末端过冲留下的，属于正常的人手散布量级，
游戏 UI 元素动辄几十 px，不影响。

---

## 7. 延迟预算

| 环节 | 延迟 |
|---|---|
| 采集卡采集（60fps） | 16.7 ms |
| USB 上行 + OpenCV 解码 | ~6 ms |
| ok-ww 识别（onnx） | 50–150 ms |
| 决策 + 轨迹生成 | 1–3 ms |
| TCP 局域网（树莓派） | 1–5 ms |
| 写 `/dev/hidg` + USB 下行 | ~2 ms |
| **闭环合计** | **约 80–180 ms** |

裸跑（同机截图 + SendInput）大约 105 ms。多出来的部分是采集卡和网络。
鸣潮的战斗自动化能吃下这个量级；需要极限操作的场景（弹反帧）会吃力。

从虚拟机开加速器、走 Wi-Fi、树莓派用 Pi Zero 一代，都会再往上加。

---

## 8. 这套方案拦住了什么、没拦住什么

**拦住了**（ACE 在物理机上的视角）：

| ACE 的检测面 | 虚拟机方案下的结果 |
|---|---|
| `EnumWindows` + `GetWindowText` 找 `ok-ww` 窗口 | 窗口在另一台机器，枚举不到 |
| `NtQuerySystemInformation` 拿进程表 + `ReadProcessMemory` | python.exe 不在物理机的进程表里 |
| `Module32First` 看 onnxruntime+cv2 模块组合 | 同上，模块在 VM 里 |
| FLTMGR 看 `~\ok-ww\logs\` 被打开 | 路径在 VM 硬盘上，物理机文件系统没这个文件 |
| PDH 采 python 的 CPU/句柄/工作集 | 采不到这个进程 |
| 输入注入标志 `LLMHF_INJECTED` | 树莓派的 HID 是真 USB 报告，无标志 |

**没拦住**：

1. **vmx 进程在物理机的进程表里**。ACE 有 Ring0，理论上能用
   `MmCopyVirtualMemory` 读 vmx 的私有内存 —— 那里面就是 ok-ww 的堆。
   要真做到这一点，它得先理解 VM 的内存布局、绕过 EPT，成本很高，
   但"成本高"不等于"不可能"。这是本方案最大的残余风险。
2. **行为模型还在**。`AntiBot::AntiBotAdapter` 那一套看的是操作序列本身。
   轨迹拟人化（贝塞尔 + Fitts + 对数正态）是在跟它对抗，不是消灭它。
3. **采集卡是新的硬件特征**。一台从没插过采集卡的机器突然多出一个
   UVC 设备 —— 正常玩家也可能有（直播），但它确实是个变化。
4. **网络侧照旧**。一号一机、固定住宅 IP、版本更新后 72 小时别开，
   这些规矩一条都不能省，跟方案无关。
5. **驱动热更新**。`ace-drc.dat` 是个 ZIP，里面是按版本分的 `ACE-ADVT` /
   `ACE-BASE` —— 它自己能换驱动。今天不在检测面的东西，下周可能在。

一句话：**这套东西买的是"检测面消失"，不是"隐身"**。它把 ACE 能找到
你的路径从"读一个进程的内存"变成了"做虚拟机内存取证"，差距很大但有限。

---

## 9. 自检清单

跑之前逐条打勾：

- [ ] `python3 pi_side/hidg.py` 在游戏机上光标真的动了
- [ ] `bridge_server.py --mode gadget` 起来后，VM 里 `net_hid.py --probe <pi-ip>` 回 PING OK
- [ ] `python vm_side/install_vm.py` 里 `ok: true`、`capture` 是 `card#0 ...`、`hid` 是 `net_hid ...`
- [ ] VM 分辨率 = 游戏机输出分辨率 = 采集卡分辨率
- [ ] 采集卡没被 OBS / 相机 App 占着（占了就打不开）
- [ ] 动一下鼠标，游戏机上的光标跟着动（不是 VM 里的光标）
- [ ] ok-ww 窗口不全屏、不抢焦点
- [ ] `VirtualCursor.clamped` 长时间跑没爆涨

---

## 10. 排错

| 症状 | 原因 |
|---|---|
| `打不开采集设备 index=0` | 被 OBS/相机占用，或 index 不对。`python capture_card.py --list` |
| 抓到全黑 | 采集卡没信号（HDMI 没插好 / 游戏机没输出 / EDID 没协商上） |
| `net_hid: 连上了但不回应 PING` | 端口对了但服务不是 bridge_server（可能连到别的） |
| 光标乱飞 | 记账漂了。重新 `_home()`；检查 VM 和游戏机分辨率是否一致 |
| 点偏了固定的几十 px | 采集卡做了缩放/裁切（有些便宜卡会裁掉边框）。对比一下实际画面边缘 |
| 按键卡住不放 | 网络断时 `IDLE_RELEASE` 5 秒才生效。可以调小 `bridge_server.py` 里的 `IDLE_RELEASE` |
| 游戏进不去（黑屏/闪退） | 那是物理机的问题，跟本方案无关 —— 先确认裸机能进 |
