# ok-ww 抗检测改造

> 目标：在**本地隔离环境**里跑 ok-ww（[ok-oldking/ok-wuthering-waves](https://github.com/ok-oldking/ok-wuthering-waves)），把"脚本特征"从客户端和服务端两侧都抹掉。
> 本目录只做技术实现，不改 ok-ww 一行源码——靠 `PYTHONPATH` + `sitecustomize` 注入。

---

## 0. 先说结论（这决定你该做哪一档）

社区里那句"**只要开着 ok-ww 就封**"（[NGA 讨论](https://ngabbs.com/read.php?tid=47900535)）是判断链路的关键：

- 如果封号来自**行为**（点击太规律、24 小时不下线），那"开着不动"不会封；
- 实际反馈是"开着就封" → 说明命中点在**客户端侧特征上报**：进程名 / 窗口标题 / 落盘路径 / 加载的模块 / 可疑 DLL 名，被反作弊扫到后随对局心跳一起上报，服务端按名单批量处理。

结论，按收益排序：

| 档位 | 做法 | 对"开着就封"的效果 |
|---|---|---|
| **A 物理隔离** | 游戏在主机，ok-ww 在副机，靠采集卡看画面、靠 USB HID 桥发操作 | **决定性**。主机上零进程、零文件、零注入，游戏侧只看到一只真键鼠 |
| B 单机驱动隔离 | 输入走 Interception / ViGEmBus，采集走 DXGI，窗口 offscreen，日志进临时目录 | 明显降命中，但 python 进程 + onnxruntime 模块仍在内存里 |
| C 单机纯软件 | 只换输入时序 + 改标题 | 只挡人工举报和行为模型，挡不住静态扫描 |

**只要还想在这台机器上跑 ok-ww，就按 A 部署。**下面 B/C 的代码是 A 的组成部分，也是 A 不可用时的降级。

---

## 1. 封号链路逐条对照

| # | 检测面 | ok-ww 默认表现 | 本方案处置 | 代码位置 |
|---|---|---|---|---|
| 1 | 进程名 | `python.exe` / `ok-ww.exe` | 随机名副本 + `pythonw.exe` 无控制台 | `stealth_launch.ps1 -WorkCopy` |
| 2 | 父进程 | `cmd.exe` / 终端 | ShellExecute 由 explorer 拉起 | `stealth_launch.ps1` §6 |
| 3 | 窗口标题/类名 | `OK-WW` / `Qt5xxxQWindowIcon` | 随机业务名 / 隐藏 / offscreen | `qtcloak.py` |
| 4 | 落盘路径 | `~\ok-ww\logs\*.log`、`configs\` | 全量重定向到 `%TEMP%\.<rand>`，退出即删 | `logcloak.py` |
| 5 | 自更新外连 | 拉 `ok-script.com` / github raw，UA=`python-requests` | DNS 层拦截 + UA 改浏览器 | `netblock.py` |
| 6 | 输入注入标志 | SendInput → `LLKHF_INJECTED`，连发间隔 0 | 硬件 HID 桥（无标志）> Interception 驱动 | `hid_bridge.py` / `arduino/` |
| 7 | 截图调用 | mss → GDI BitBlt，每秒数十次 | DXGI Desktop Duplication（OBS 同款） | `capture.py` |
| 8 | 落点/轨迹 | 直接 setCursorPos 或瞬移 | 贝塞尔 + 弓形 + 过冲回拉 + 高斯散布 | `human_input.py` |
| 9 | 时序分布 | 恒定间隔 | Fitts 定律 + 对数正态 + 2% 长尾发呆 | `human_input.py` / `behavior.py` |
| 10 | 在线时长 | 连续 8~12 小时满负荷 | 日程采样：主会话 lognormal(78min)，30% 概率下午短会话，7% 整晚不玩 | `behavior.py` |
| 11 | 设备/IP | 机场节点频繁跳变 | 固定住宅 IP，不挂代理，一机一号 | 见 §6 |

第 6 条是**权重最高**的一条：即使前十项全中，只要输入是真实 HID，行为模型还能把你判成"手速快的真人"；反过来，输入是 SendInput，其他都洗干净也没用。

---

## 2. 三套部署拓扑

### A. 双机物理隔离（推荐）

```
┌────────────── 主机（只跑游戏）──────────────┐        ┌──────── 副机（跑 ok-ww）────────┐
│  鸣潮 客户端                                │        │  okww_shim + ok-ww              │
│    ↑ USB HID 报告（真实设备）               │        │    ↑ 采集卡帧                   │
│  ┌──────────────────────┐                   │        │  ┌──────────────────────┐       │
│  │ RP2040 / Pro Micro   │◄── USB 串口 ──────┼────────┼──│ hid_bridge.py        │       │
│  │ okww_hid.ino         │   (副机 USB)      │        │  │ capture.py(DXGI/采集卡)│      │
│  └──────────────────────┘                   │        │  └──────────────────────┘       │
│                                             │        └─────────────────────────────────┘
│  HDMI 输出 ──► 采集卡(MS2130/Elgato) ──► 副机 USB
└─────────────────────────────────────────────┘
```

- 主机上**唯一**新增的东西是一个 USB HID 设备 —— 真玩家也必须有。
- 反作弊枚举进程、扫窗口、查模块、看句柄，全部落空。
- 采集卡延迟 30~80ms，对回合制日常够用；对闪避/连招不够，那种战斗交给游戏内自动战斗。
- 副机可以用一台旧笔记本 / 迷你主机 / 树莓派 5（跑 Linux + onnxruntime 也够）。

### B. 单机驱动隔离

```
游戏 ◄── Interception 驱动（内核态虚拟键鼠，无 INJECTED 标志）
     ◄── DXGI 复制（OBS 同款 API）
ok-ww（pythonw，窗口 offscreen，日志进 %TEMP%）
```

`Interception` 需要装 [interception 驱动](https://github.com/oblitum/Interception) 并把 `interception.dll` 放到 PATH。比 SendInput 干净，但驱动本身也是可枚举的（`interception.sys` 加载记录）。

### C. 单机纯软件

只做行为层 + 窗口/日志/外连抹除。应急用。

---

## 3. 快速上手

```powershell
# 0) 依赖
pip install pyserial numpy opencv-python dxcam

# 1) 自检（不启动 ok-ww）
python -c "import okww_shim, json; print(json.dumps(okww_shim.install(quiet_stdio=False), ensure_ascii=False, indent=2))"

# 2) 生成 7 天作息表，看看像不像人
python -m okww_shim.behavior schedule.json

# 3) 启动（B/C 档）
.\stealth_launch.ps1 -OkwwPath D:\tools\ok-ww -Mode hidden -Backend hid

# 4) 启动（更狠：随机名副本 + 屏蔽更新域名 + 清历史痕迹）
.\stealth_launch.ps1 -OkwwPath D:\tools\ok-ww -Mode ghost -Backend hid -WorkCopy -BlockHosts -WipeTraces
```

`-Mode` 三档：

| 值 | 行为 | 适用 |
|---|---|---|
| `gui` | 窗口正常显示，标题每次随机 | 要手动点界面调参 |
| `hidden` | 窗口不显示，事件循环照跑 | **日常推荐** |
| `ghost` | `QT_QPA_PLATFORM=offscreen`，不创建原生窗口 | 全自动挂机 |

`-Backend` 三档：`hid`（硬件桥，需插板子）> `interception`（驱动）> `sendinput`（兜底，最脏）。

---

## 4. 文件说明

| 文件 | 作用 |
|---|---|
| `sitecustomize.py` | Python 启动钩子，`PYTHONPATH` 指向本目录即自动注入，ok-ww 零改动 |
| `okww_shim/__init__.py` | `install()` 总装：替换 `mss`/`pydirectinput`/`pyautogui`/`mouse`，接日志/外连/窗口 |
| `okww_shim/human_input.py` | 人类化输入层。三次贝塞尔轨迹 + 弓形 + 过冲回拉 + 高斯落点 + Fitts 时长 + N(85,25)ms 按键 dwell |
| `okww_shim/hid_bridge.py` | 硬件 HID 桥串口协议（分片 ±127、6KRO 键盘、xor 校验、VK→HID 全表） |
| `okww_shim/capture.py` | 采集层：DXGI → 真 mss → 复用 DC 的 GDI，三级回退 |
| `okww_shim/netblock.py` | DNS 层拦截 ok-script / github 域名，连接立即失败，不产生外连流量 |
| `okww_shim/logcloak.py` | `logging.FileHandler` 全量改写到 `%TEMP%\.<rand>`，退出删目录 |
| `okww_shim/qtcloak.py` | 窗口标题随机化 / 隐藏 / offscreen，持续守护新建窗口 |
| `okww_shim/behavior.py` | 作息采样器 + 对数正态节拍器 |
| `stealth_launch.ps1` | 启动器：环境变量、随机名副本、explorer 父进程、hosts、痕迹清理 |
| `arduino/okww_hid/okww_hid.ino` | RP2040（Adafruit TinyUSB）/ ATmega32u4（内置 HID）双分支固件 |

---

## 5. 固件烧录

**RP2040（推荐，CDC 稳、便宜）**
1. Arduino IDE 装 *Raspberry Pi Pico/RP2040* core
2. 板子选 `Raspberry Pi Pico`，Tools → USB Stack 选 **Adafruit TinyUSB**
3. 打开 `arduino/okww_hid/okww_hid.ino`，上传
4. 想在设备管理器里显示成普通键鼠：`-DUSB_MANUFACTURER="Generic" -DUSB_PRODUCT="USB Composite Device"`

**Pro Micro / Leonardo（ATmega32u4）**
1. 板子选 `Arduino Leonardo`，直接上传
2. 烧完串口会变号，插上后 `python -c "from okww_shim.hid_bridge import HidBridge; print(HidBridge.candidates())"` 确认端口

烧完自检：
```powershell
python -c "from okww_shim.hid_bridge import HidBridge; b=HidBridge.open(); print(b.ping()); b.close()"
```

---

## 6. 加载进 ok-ww 的方式

ok-ww 用 `pydirectinput` 点界面、用 `mss` 截图、用 `requests` 查更新。三个都在 `sys.modules` 里被顶掉，调用方无感：

```python
sys.modules["pydirectinput"] = okww_shim.human_input   # moveTo/click/press/keyDown...
sys.modules["mss"]           = okww_shim.capture      # mss().grab({...}) -> BGRA
sys.modules["pyautogui"]     = _HumanProxy(...)
sys.modules["mouse"]         = _MouseProxy(...)
```

若某个版本直接 `frida`/`ctypes` 调 `SendInput`，在 `human_input.py` 顶上挂一层：
```python
import ctypes, okww_shim
ctypes.windll.user32.SendInput = okww_shim.human_input.get().backend._emit  # 归一到 HID
```

---

## 7. 账号与网络侧（代码救不了的部分）

1. **一号一机**。同机多号 = 同设备指纹，一封一串。
2. **IP 不要跳**。机场节点轮换是最强的机器信号之一，比脚本本身还准。固定一条家宽，脚本和手动登录用同一条出口。
3. **先小号跑两周**。新号 + 新设备 + 新行为同时出现，本身就是异常；让账号先"养"出正常历史。
4. **别在活动期开**。版本更新后 72 小时是风控最严的时候（[停更公告](https://www.taptap.cn/moment/867161461003126587) 也在这个窗口）。
5. **停机也是特征**。真人会缺席。`behavior.py` 里 7% 的"整晚不玩"不是凑数，是让在线时长分布右尾变短。

---

## 8. 已知限制（别指望它全挡）

- 反作弊若做**内存字符串扫描**（找 `ok-ww`/`ok-script` 的模块符号、找 onnx 模型路径），单机方案挡不住 → 只有 A 档物理隔离能解决。
- 采集卡方案有 30~80ms 延迟，实时战斗场景（闪避帧）不可用。
- `dxcam` 在部分驱动/多屏混合 DPI 下会返回 `None`，`capture.py` 会自动回退，但回退到 GDI 时全屏独占客户端是黑图——那就必须用采集卡。
- `qtcloak` 的 `ghost` 模式依赖 Qt 的 offscreen 平台插件；个别 PySide6 版本会缺 `qoffscreen.dll`，此时退回 `hidden`。
- `SendInput` 兜底永远带 `INJECTED` 标志，无法消除，只能靠前两条。

---

## 9. 自检清单

- [ ] `python -c "import okww_shim; print(okww_shim.status())"` 里四个模块都指向本目录
- [ ] `HidBridge.open().ping()` 返回 `True`（插了板子才有）
- [ ] `capture.mss()` 的 `backend_name` 是 `dxgi`（装 `dxcam` 后）
- [ ] 任务管理器里看不到 `ok-ww` 字样，只有一个随机名进程
- [ ] `%USERPROFILE%` 下没有 `ok-ww` / `.ok-ww` 目录
- [ ] `Get-Process pythonw | ft StartTime,Path` 的父进程是 `explorer`
- [ ] 任务跑 40 分钟后有一次真实休息（`behavior.py` 的 chunks）
