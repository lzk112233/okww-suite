# okww-rename — 单机改名脱敏

不装虚拟机、不接硬件，只在这台机器上把这套 ok-ww 的"身份"洗掉。

---

## 先回答"行不行"

**注册表：没用。改了个寂寞。**

ACE 不读 `HKCU\Software\ok-ww`。它读的是进程表、进程内存、模块列表、窗口标题、HID 报告流。
注册表那两条键（`ok-ww` 和 `Uninstall\ok-ww`）只在服务端拿到你的机器快照之后当作标签用，
删掉它们不影响当场判定，只影响"事后取证时能不能直接对上是 ok-ww"。

**改名：有用，但只砍一半。**

这套东西不是单 exe，是 PyAppify 三件套：

```
ok-ww.exe (9.92 MB)                     ← 门牌，改这个没用
 └→ data\apps\ok-ww\python\pythonw.exe  ← 真正在跑的进程 (0.1 MB stub)
      └→ data\apps\ok-ww\working\main.py ← 真正在跑的代码
```

改 `ok-ww.exe` 的名字等于换门牌不换房子。**真正有用的是把 `pythonw.exe` 复制成
`AudioHost.exe`、然后用 `AudioHost.exe` 启动** —— 实测 `sys.executable` 会自动跟着变，
不用改一行代码：

```
> copy python\pythonw.exe python\AudioHost.exe
> python\AudioHost.exe -c "import sys; print(sys.executable)"
C:\Users\lzk11\AppData\Local\ok-ww\data\apps\ok-ww\python\AudioHost.exe
```

同时任务管理器里的进程名、`Win32_Process.CommandLine` 里 ACE 能从 EPROCESS/PEB 读到的
路径，一起变成 `AudioHost.exe`。

**但这套组合的其它特征还在**，见下面的对照表。

---

## 八条检测面，改名砍掉几条

| # | 检测面 | ACE 用什么看 | 裸跑 | 改名前 | 改名后 |
|---|---|---|---|---|---|
| 1 | 进程名 | `NtQuerySystemInformation` | `pythonw.exe` ❌ | `ok-ww.exe` → pythonw | **`AudioHost.exe`** ✅ |
| 2 | 安装路径字符串 | `ReadProcessMemory` / 命令行 | `AppData\Local\ok-ww` ❌ | 同 | **`Microsoft\DeviceSync\app`** ✅ |
| 3 | 窗口标题 | `EnumWindows` + `GetWindowText` | `ok-ww` ❌ | 同 | 仍可能是 `ok-ww` ⚠️ |
| 4 | 注册表残留 | 一般不查 | 有 ❌ | 有 ❌ | 无 ✅ |
| 5 | 模块组合 | `Module32First` | `cv2.pyd`+`Qt6WebEngineCore.dll`+`openvino` ❌ | 同 ❌ | 同 ❌ |
| 6 | 内存字符串 | `ReadProcessMemory` 扫堆 | `ok_ww` / `ok-script` ❌ | 同 ❌ | 同 ❌ |
| 7 | 磁盘源码 | FLTMGR 文件打开记录 | `working\main.py` 明文 ❌ | 同 ❌ | 同 ❌ |
| 8 | 输入注入 | `HIDPARSE.SYS` + `LLMHF_INJECTED` | SendInput ❌ | 同 ❌ | 同 ❌ |
| + | 管理员权限 | 完整性级别 | `admin:true` ❌ | 同 ❌ | **`admin:false`** ✅ |

**从 9 条砍到 4 条**（5/6/7/8 砍不掉）。第 3 条窗口标题要看 ok-ww 自己怎么设的，
用 `okww_shim.qtcloak` 才能压住。

剩下这四条的信息论含义：

- #5 模块组合：`cv2 + QtWebEngine + openvino` 在同一个人进程里出现，是"脚本/自动化"的机器画像。
  改文件名、改目录、改注册表都不改变这个组合。要改只能改依赖本身。
- #6 内存字符串：`working\main.py` 是明文源码，import 之后 `.pyc` 也在堆里。
  进程内存里 `ok_ww` 出现在类名、模块名、路径常量里。
- #7 磁盘源码：`main.py` 就躺在 `working\` 目录里，`ReadProcessMemory` 拿到路径就能顺着读。
- #8 输入注入：`SendInput` 产生的事件在内核 HID 层带 `LLMHF_INJECTED` 标志，
  而 `HIDPARSE.SYS` 收的物理报告不带。两路对不上，这条只能靠硬件桥解决。

**结论：单机改名把"一眼可见"降成"要么读内存要么分析模块"。**
它不是隐身，它把主判据从"进程名/路径"移到"进程内容"——后者代价高、误报大，
但只要你继续用同一个 python 跑同一份 cv2 代码，这关就还在。

---

## 用法

```powershell
cd D:\ai\dfy\okww-rename
.\rename_okww.ps1                        # dry run：只打印计划，不动任何东西
.\rename_okww.ps1 -Mode copy             # 复制到新目录（2 GB / 17089 文件，安全，原装不动）
.\rename_okww.ps1 -Mode move             # 原地搬（秒级，可用 -Rollback 还原）
.\rename_okww.ps1 -Rollback              # 撤销上一次 -Mode move
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `-Source` | `%LOCALAPPDATA%\ok-ww` | 原装根目录 |
| `-Target` | `%LOCALAPPDATA%\Microsoft\DeviceSync\app` | 新根目录（借 Microsoft 的目录名） |
| `-AppName` | `AudioHost` | 替换名，同时用于 exe / 注册表 / 窗口 |
| `-Mode` | `dryrun` | `dryrun` / `copy` / `move` |

---

## 脚本改了什么

1. 停掉从旧目录起的所有 python/pythonw 进程
2. 注册表两条键备份成 `.reg` 后删除
3. 整树搬到新根（copy 走 robocopy，排除 `.git` `logs` `__pycache__` `*.pyc`）
4. **复制** `python\pythonw.exe` → `python\AudioHost.exe`
   —— 必须在同一目录，因为 `python312.dll` 就在旁边，挪走会静默启动失败
5. `ok-ww.exe` 改名成 `AudioHost.exe`（保留，但启动脚本绕过它）
6. 重写 `.pyappify-shortcut.py` 的 `_main_script` / `_app_name`
7. 重写 `app.json`：`name=AudioHost`、`website=""`、`update_method=NONE`、
   `show_add_defender=false`、**`admin=false`**
8. 删 `repo\.git`（126 MB 源码史，里面全是 ok-ww 的提交记录）
9. 清 `logs\*`（ACE 的 FLTMGR 看得见这些文件被反复打开）
10. 生成 `start-AudioHost.cmd`

启动方式变了 —— **不要再用 `ok-ww.exe` 启动**，用生成的 cmd：

```
data\apps\ok-ww\python\AudioHost.exe  data\apps\ok-ww\.pyappify-shortcut.py
```

走 shortcut 而不是直接跑 `main.py`，因为 shortcut 才是做环境清理的那一层
（pop 掉 13 个 PYTHON* 变量、把 PATH 重置成 5 个系统目录）。
它内部 `subprocess.run([sys.executable, main_script])`，`sys.executable` 已经是
`AudioHost.exe`，所以子进程也是新名字。

---

## 配套（这套改名单独用不够）

第一轮做的 `D:\ai\dfy\okww-stealth\` 跟这个配合，覆盖改名碰不到的面：

```powershell
$env:PYTHONPATH = "D:\ai\dfy\okww-stealth"   # 注入 sitecustomize
$env:OKWW_CLOAK = "hidden"                    # 窗口标题 + 隐藏
$env:OKWW_INPUT_BACKEND = "sendinput"         # 或 hid（有硬件桥时）
```

- `qtcloak` → 补 #3 窗口标题
- `logcloak` → 日志里不再出现 `ok-ww`，落盘到随机临时目录
- `netblock` → 断掉 `ok-script.com` 自更新与上报
- `human_input` → 补 #8 的轨迹部分（但注入标志还在，硬件桥才能消）

---

## 已知限制

1. **`pythonw.exe` 的 PE 版本资源还写着 `Python / Python Software Foundation`**
   —— 上面实测确认过。要抹掉得改 PE 资源节，那是另一个工程量，且改完签名/校验和
   对不上会让 Windows 报"文件可能损坏"。改名不改 PE。
2. **`admin=false` 可能让窗口操作失败**。如果游戏本身以管理员权限运行，
   UIPI 会拒绝低完整性进程往高完整性窗口发消息。症状是能截图但点击无效。
   这时把 `app.json` 里的 `admin` 改回 `true`，代价是那条特征回来。
3. **`update_method=NONE` 会让 ok-ww 自己检查更新失败**，可能弹错误框或者启动时卡一下。
   属于预期行为——它本来就该断网。
4. **`repo\.git` 删掉后，ok-ww 的 "检查更新" 和 "回滚版本" 功能失效**。
5. **窗口标题**：如果 ok-ww 硬编码 `setWindowTitle("ok-ww")`，改名救不了，
   靠 `OKWW_CLOAK=hidden` 或 ghost 模式。

---

## 实测记录

改名可行性（本机 py 3.12.10）：

```
> copy python\pythonw.exe python\_probe_abc.exe
> python\_probe_abc.exe -c "import sys;open(r'%TEMP%\_who.txt','w').write(sys.executable)"
sys.executable = C:\...\python\_probe_abc.exe
sys.version    = 3.12.10 (tags/v3.12.10:0cc8128, Apr  8 2025, 12:21:36) [MSC v.1943 64 bit (AMD64)]
进程名          = _probe_abc   (Path = ...\python\_probe_abc.exe)
CommandLine    = "...\python\_probe_abc.exe" -c "import time;time.sleep(8)"
```

第一次把 `pythonw.exe` 复制到 `D:\...\_probe\` 下启动，**没有任何输出也没有进程** ——
因为 stub 找不到同目录的 `python312.dll`。这就是第 4 步必须同目录的原因。

安装体积（决定 copy 要多久）：

| 目录 | 大小 | 处理 |
|---|---|---|
| `data\apps\ok-ww\python` | 1213.6 MB | 保留（cv2 82MB / Qt6WebEngineCore 195MB / openvino 78+44MB 都在这里） |
| `data\apps\ok-ww\repo` | 229.6 MB | 保留，删 `.git`（126.7 MB pack） |
| `data\apps\ok-ww\working` | 86.8 MB | 保留（main.py 明文 + echo.onnx 36MB） |
| `cache` | 516.6 MB | 可删 |
| `EBWebView` | 56.9 MB | 保留（Qt WebEngine 运行时） |
| `logs` | 3.1 MB | 清空 |
| **合计** | **2.07 GB / 17089 文件** | copy 模式大概 1–3 分钟 |

---

## 文件

```
rename_okww.ps1     改名 + 搬目录 + 清注册表 + 生成启动脚本
_state\             运行时生成：backup-<时间戳>\ 和 manifest-<时间戳>.json
```
