# ok-ww 抗检测改造套件

鸣潮（Wuthering Waves）自动化工具 [ok-ww](https://github.com/ok-oldking/ok-wuthering-waves) 在 ACE 反作弊环境下的改造记录。

四条路线、全部真机实测、含检测机制逆向分析与可复现的验证工具。

> 所有结论都有日志、截图或代码行号支撑，不是推测。文中出现的 `working\…` 指 ok-ww 的安装目录 `%LOCALAPPDATA%\ok-ww\data\apps\ok-ww\working`。

---

## 一、结论先行

**封号的主判据不是行为，是环境特征。** 依据：NGA 上大量反馈是「okww 只要开着就会封号」，挂机不操作也封 —— 行为模型解释不了这个现象，只有静态特征上报能解释。

**最致命的一条破绽**（也是本仓库 `fg/` 存在的理由）：

`ok\device\interaction_methods\post_message.py` 每次发键鼠消息之前，都会先 `PostMessage` 一条伪造的窗口激活：

```python
# post_message.py:144
self.post(win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0, hwnd=hwnd)
# post_message.py:147
self.post(win32con.WM_ACTIVATE, win32con.WA_INACTIVE, 0, hwnd=hwnd)
# post_message.py:149
def try_activate(self):
    base_hwnd = self.hwnd_window.hwnd
    current_hwnd = self.hwnd
    self.activate(base_hwnd)
    if current_hwnd != base_hwnd:
        self.activate(current_hwnd)      # 一次可能发两条
```

调用点遍布 `send_key_down`（:41）、`input_text`（:84）、`scroll`（:97）、`update_mouse_pos`（:203，无条件调）。所以按键、点击、移动，每一下都带。

游戏侧只需要三行就能抓到：

```c
case WM_ACTIVATE:
    if (GetForegroundWindow() != hwnd)   // 真实激活不可能窗口不在前台
        report_fake_activation();
```

`GetForegroundWindow()` 读的是内核 `gpqForeground`，用户态伪造不了。真实的 `WM_ACTIVATE` 由 `win32k!xxxActivateWindow` 以 SendMessage 语义同步投递，只在该窗口真被激活时发生。

**这解释了「挂着不动也封」—— 不用操作，ok-ww 开着就在发这条消息。**

**当前最优解**：改成真前台（`PyDirect`）。零 `PostMessage`、零 `WM_ACTIVATE`，全部走 `SendInput`。代价是任务运行时独占前台。

---

## 二、四个模块

| 目录 | 做什么 | 状态 |
|---|---|---|
| **`fg/`** | **主线**。把 ok-ww 从 PostMessage 改成真前台，含 hook 版与配置版两条路 | 真机验证通过 |
| `stealth/` | 纯软件层抹除：进程名、窗口标题、日志、网络、输入人类化 | 自检通过 |
| `vm/` | 双机隔离：游戏物理机 + 脚本虚拟机 + 采集卡 + 树莓派 HID | 链路实测通过 |
| `rename/` | 单机改名：注册表 + 进程名 + 安装路径清洗 | 脚本就绪 |

四条路线是递进的：`stealth` 降低静态特征 → `rename` 再削一层 → `fg` 解决输入伪造 → `vm` 才是彻底隔离。

### `fg/` —— 前台模式（推荐）

两条实现路径，选一条即可：

**A. 配置切换（干净，零补丁）**

改两处，**缺一不可**（`DeviceManager.py:85-105` 先用 config 赋值，再用 devices.json 的持久值比对覆盖，只改后者无效）：

```python
# working\config.py:219-221
'hwnd_class': 'UnrealWindow',
'interaction': 'PyDirect',
```

```json
// working\configs\devices.json:9
"interaction": "PyDirect"
```

`PyDirectInteraction`（`pydirect.py`，162 行）读过了：零 `PostMessage`、零 `WM_ACTIVATE`、零 `SetForegroundWindow`，全部 `pydirectinput` → `SendInput`。每个动作前 `if not self.clickable(): return`，游戏不在前台就静默不发。

启动后主界面顶栏应显示 `鸣潮  2560x1440  WGC  PyDirect`。日志前缀会从 `DailyTask:left_click <名字> (x,y)` 变成 `pydirect:left_click (x,y)`。

**B. Hook 切换（保留 PostMessage 类但换成真前台）**

`okww_fg/interaction.py` 把 `PostMessageInteraction` 的六个方法（`post` / `activate` / `deactivate` / `try_activate` / `send_key_down` / `send_key_up`）替换成真前台 + SendInput 的实现。通过 `sitecustomize.py` 注入，**ok-ww 源码一字不改**。

装法：

```powershell
$sp = "C:\Users\<你>\AppData\Local\ok-ww\data\apps\ok-ww\python\Lib\site-packages"
Copy-Item .\fg\okww_fg "$sp\okww_fg" -Recurse
Copy-Item .\fg\sitecustomize.py "$sp\sitecustomize.py"
```

验证：运行时 `import okww_fg; okww_fg.status()` 应返回 `post → _fg_post` 等六个替换。

### `stealth/` —— 软件层抹除

`sitecustomize.py` + `okww_shim`，靠 `PYTHONPATH` 注入，不碰 ok-ww。

| 文件 | 作用 |
|---|---|
| `okww_shim/human_input.py` | 人类化输入：三次贝塞尔轨迹、Fitts 定律时长、dwell/press 正态抖动 |
| `okww_shim/capture.py` | mss 兼容门面，dxgi → mss → gdi 三级回退 |
| `okww_shim/hid_bridge.py` | 串口 HID 协议（配合 Arduino / 树莓派） |
| `okww_shim/netblock.py` | 拦 `ok-script.com` 等 10 个域名的 DNS |
| `okww_shim/logcloak.py` | 日志重定向到 `%TEMP%\.<uuid>` + 特征串替换 |
| `okww_shim/qtcloak.py` | 窗口标题伪装，三档 light/hide/ghost |
| `okww_shim/behavior.py` | 在线时段规划（对数正态分布，避免固定作息） |
| `arduino/okww_hid/okww_hid.ino` | RP2040 / ATmega32u4 双分支固件 |
| `stealth_launch.ps1` | 编排器，`ShellExecute(…,'open',0)` 让父进程是 explorer |

### `vm/` —— 双机物理隔离

方向是**脚本进虚拟机，游戏留物理机**，不是反过来。

```
物理机（游戏 + ACE）              虚拟机（ok-ww + python）
  鸣潮 ──HDMI──> 采集卡 ──USB直通──> capture_card.py  ← 画面进 VM
  游戏窗口 <──USB HID gadget── 树莓派 <──TCP:8765── net_hid.py  ← 键鼠出 VM
```

物理机上只剩两个 USB 节点：一个 UVC 采集卡、一个伪装成 `0x046D:0xC31C` 的 HID 键鼠。这条不查 —— 直播主播全是这个配置。

| 文件 | 作用 |
|---|---|
| `vm_side/install_vm.py` | 胶水层，顺序必须是 shim → capture → hid → cursor |
| `vm_side/capture_card.py` | 采集卡截图，mss 兼容；先设 FOURCC 再设分辨率 |
| `vm_side/net_hid.py` | 网络 HID 客户端，帧间隔带 ±25% 抖动 |
| `pi_side/hidg.py` | Linux configfs USB HID gadget |
| `pi_side/bridge_server.py` | TCP → HID 桥，断连与 5s 无帧自动松手 |
| `pi_side/setup_gadget.sh` | 树莓派一键配置（Pi 5 的 USB-C 无 device 模式，用 Pi 4 / Zero 2 W） |

### `rename/` —— 单机改名

`rename_okww.ps1 -Mode dryrun|copy|move`，含 `-Rollback`。

能改掉的：进程名（复制 `pythonw.exe` 成别的名字，`sys.executable` 会自动跟随）、安装路径、注册表两项、`app.json` 里的 `ok-script.com` 与 `git_url`、`repo\.git` 的 126MB 源码史。

改不掉的：模块组合（`cv2.pyd` + `Qt6WebEngineCore.dll` + `openvino`）、内存里的 `ok_ww` 字符串、磁盘上 `working\main.py` 明文、`SendInput` 注入标志。

**关键前提**：`ok-ww.exe` 只是 9.92MB 的 launcher，真身是 `data\apps\ok-ww\python\pythonw.exe`（0.1MB）+ `working\main.py`。改名要动的是后者。

---

## 三、ACE 检测机制（本机逆向实测）

组件位于 `C:\Program Files\AntiCheatExpert\`。关键发现：**导入表是干净的，能力全靠 `MmGetSystemRoutineAddress` 动态解析** —— 只看导入表会误判「它只会联网」。

`ACE-BASE.sys` 静态只导入 `FLTMGR.SYS`（文件微过滤）、`HAL.dll`、**`HIDPARSE.SYS`**（内核 HID 报告解析）、`NDIS.SYS`、`ntoskrnl.exe`。

各模块从字符串挖出的能力：

| 模块 | 动态解析到的关键 API |
|---|---|
| `SGuard64.exe` | `OpenProcess` / `CreateToolhelp32Snapshot` / `Module32First` / `CreateRemoteThread` / `GetClassName` / `GetWindowThreadProcessId` |
| `ACE-Safe.dll`（注入游戏进程） | `GetForegroundWindow` + `BitBlt` + `CreateCompatibleBitmap`；**`AntiBot::AntiBotAdapter` / `tss_anti_sdk::VAntiClientAdapter`（35 处 bot 符号）** + `HWCryptoHook_*` |
| `ACE-SSC64.dll` | `ReadProcessMemory` + **`WriteProcessMemory`** + `VirtualAllocEx`；**`RegisterRawInputDevices` + `GetRawInputDeviceList` + `GetRawInputDeviceInfo`** |
| `ACE-CSI64.dll` | `NtQuerySystemInformation` + `NtQueryInformationProcess` + `NtQueryVirtualMemory` + `NtQueryObject` |
| `ACE-Service64.exe` | PDH 计数器（Handle Count / Private Bytes / Working Set）；`HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\` 监控 |

驱动有热更新：`C:\ProgramData\AntiCheatExpert\ace-drc.dat` 是个 ZIP，实测时 18 小时前刚更新过。

**检测面按命中概率排序**：

1. 窗口枚举（ok-ww 的标题字面就是 `ok-ww`）
2. 跨进程读内存（python 堆 500MB~1.5GB，`.pyc` 里有 `ok_ww` 字符串）—— **这是「挂着不动也封」的解释**
3. 模块指纹组合（onnxruntime + cv2 + ctypes 同进程）
4. 输入注入（`HIDPARSE.SYS` 内核报告流 vs 钩子拿到的 `LLMHF_INJECTED`，双路比对）
5. 前台窗口 + `BitBlt` 截屏、`AntiBot` 行为模型
6. PDH 按进程采 CPU / 句柄
7. 文件访问（FLTMGR 看得到 `~\ok-ww\logs\` 被打开）
8. 显卡驱动层（`nvapi64.dll` / `nvldumd.dll`）

封号时序：本地只采集打标，加密上报，服务端跑批。所以「开着 → 两天后封」，且**开过一次标记就发出去了**。

---

## 四、实测数据

**UIPI 是硬门槛。** 游戏进程是管理员跑的（`Client-Win64-Shipping.exe` 提权），普通权限的 `SendInput` 和 `PostMessage` 会被系统**静默丢弃，不报错**：

```
explorer.exe                  普通权限
Client-Win64-Shipping.exe     提权(管理员)
python.exe                    普通权限
```

提权前后同一套测试（截图差分判反应，阈值 2%）：

| 测试 | 普通权限 | 管理员 |
|---|---|---|
| F1 | mean=1.53 / 0.60% 没反应 | mean=80.51 / **81.35%** 有反应 |
| Esc | mean=1.43 / 0.53% 没反应 | mean=82.02 / **79.86%** 有反应 |
| W 按住 500ms | mean=1.53 / 0.54% 没反应 | mean=18.49 / **31.22%** 有反应 |
| 鼠标移动 +200px | mean=1.45 / 0.72% 没反应 | mean=24.63 / **24.56%** 有反应 |

`app.json` 里的 `"admin": true` 就是这个原因 —— 不提权连消息都发不进游戏。

**切前台**：从 Chrome 抢到游戏，`elapsed=0.0005s`，外圈 23.9ms。游戏窗口 `hwnd=202028`，`class='UnrealWindow'`，`style=0x94000000`（无边框窗口化全屏，不是独占全屏）。

**PyDirect 跑 DailyTask**：141 次 `pydirect:left_click`，`not clickable` 拒绝 **0 次**，战斗阶段每 ~110ms 一次。

**协议帧 XOR 校验**（`AA|CMD|LEN|PAYLOAD|XOR`）：

```
MOUSE   aa 01 06 64 00 ce ff 00 01 f9
KEY     aa 02 07 02 04 00 00 00 00 00 a9
PING    aa 03 00 a9
RELEASE aa 04 00 ae
```

---

## 五、快速上手

> 只想用、不想看原理 → 直接看 [**QUICKSTART.md**](QUICKSTART.md)，两步搞定。

**第一次**（改配置，自动备份，只做一次）：

```powershell
cd fg
powershell -ExecutionPolicy Bypass -File setup.ps1          # 改两处 interaction 为 PyDirect
powershell -ExecutionPolicy Bypass -File setup.ps1 -Check   # 只看会改什么
```

**日常**（自带 UAC 提权）：

```bat
fg\run.bat                  :: 打开主界面
fg\run.bat daily            :: 跑每日任务
fg\run.bat AutoCombatTask   :: 跑任意任务
```

`setup.ps1` 会先备份到 `%LOCALAPPDATA%\ok-ww\_backup_<时间戳>\`，改完自己验证一遍；`run.bat` 会检查是否已是 PyDirect、是否管理员，没问题才启动。

**手改也行**（`run.bat` 就是替你干这两件事）：

```powershell
$py   = "$env:LOCALAPPDATA\ok-ww\data\apps\ok-ww\python\python.exe"
$work = "$env:LOCALAPPDATA\ok-ww\data\apps\ok-ww\working"
# 先把 working\config.py:221 与 working\configs\devices.json 的 interaction 改成 PyDirect
Start-Process cmd.exe -ArgumentList ('/c start "" /d "' + $work + '" "' + $py + '" main.py') -Verb RunAs
```

**注意**：必须用 `cmd /c start`（或 `run.bat` 里的 `start ""`）让 python 脱离父进程树。直接 `Start-Process` 会在命令结束时被连带收走，症状是 34 秒后日志出现 `MainWindow:Window closed exit_event.is not set False` 然后自退。

**验证工具**（`fg/tools/`）：

| 脚本 | 用途 |
|---|---|
| `probe_game.py` | 枚举窗口找游戏，`--switch` 试真切前台 |
| `probe_activation.py` | 独立复现「伪造 WM_ACTIVATE」这一判据 |
| `e2e_focus.py` | 端到端测真前台 + 真 SendInput 落字 |
| `e2e_patched.py` | 对比原版 PostMessage 与补丁版的 `WM_ACTIVATE` 计数与伪造数 |
| `key_report.py` | 逐个按键测反应（截图差分） |
| `elev_test.py` | 验证 UIPI：同一套测试在提权前后跑一遍 |

`e2e_patched.py` 要用 **ok-ww 自带的 python** 跑（系统 python 没 pywin32）：

```powershell
$env:PYTHONPATH = "D:\path\to\okww-suite\fg"
& "C:\Users\<你>\AppData\Local\ok-ww\data\apps\ok-ww\python\python.exe" fg\tools\e2e_patched.py
```

---

## 六、已知限制

1. **PyDirect 独占前台**。任务跑起来这台机器就用不了，切走它就不发（不是乱发）。这是换掉伪造激活的代价。
2. **行为层没解决**。`AntiBot` 行为模型看的是操作节奏：技能循环 100% 精准、闪避零延迟、路径永远最优。本仓库只管「什么时候玩」（`behavior.py`）和「怎么发」（`human_input.py`），不管「做什么」。要补得在决策层注入噪声，代价是效率降 10~15%。
3. **账号网络侧独立**。一号一机、固定住宅 IP、版本更新后 72 小时别开 —— 与架构无关，别的地方解决。
4. **驱动热更新**。`ace-drc.dat` 会自动更新，今天是这个特征，下周不一定是。
5. **改 config.py 会被更新覆盖**。它在 `repo` 里，ok-ww 自动更新会重置。要么用 hook 版，要么每次更新后重改。
6. `rename/` 里 `admin=false` 可能因 UIPI 让点击失效（症状是能截图但点不动），需要按游戏是否提权来决定。

---

## 七、免责

仅供学习逆向工程与 Windows 输入栈（`SendInput` / `HIDPARSE` / UIPI / WGC）的实践记录。使用后果自负，与作者无关。

## License

MIT
