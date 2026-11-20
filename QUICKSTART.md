# 最简单用法

## 它解决什么

ok-ww 原来用 `PostMessage` 往游戏窗口"塞"假键鼠消息，而且**每发一条之前都先补一条伪造的窗口激活**（`post_message.py:144/147`，`WM_ACTIVATE`）。

游戏只要在自己收到 `WM_ACTIVATE` 时问一句 `GetForegroundWindow() == 我?` —— 不是，就是伪造的。这就是"开着不操作也封"的原因。

这套东西把它换成 `PyDirect`：**真的把游戏切到前台，用 `SendInput` 发真键鼠**，一条窗口消息都不发。

---

## 第一步：装一次（只做一次）

前提：ok-ww 已经装好，能正常打开（默认在 `%LOCALAPPDATA%\ok-ww`）。

在 `fg` 目录里**按住 Shift 右键 → 在此处打开 PowerShell 窗口**，跑：

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

看到 `DONE` 就成。它做的事：

- 找到 ok-ww 的 `config.py` 和 `devices.json`
- 先把两份原文件备份到 `%LOCALAPPDATA%\ok-ww\_backup_<时间戳>\`
- 把两处 `interaction` 改成 `PyDirect`
- 改完自己验证一遍

只想看会改什么、不动手：

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1 -Check
```

### 可选：让以后双击不再问 UAC

以**管理员**身份开一个 PowerShell（右键 → 以管理员身份运行），进到 `fg` 目录，跑：

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1 -InstallTask
```

它注册两个计划任务（`okww-suite-gui` / `okww-suite-daily`，都是以最高权限运行）。之后 `run.bat` 会优先用它们启动，**不再弹 UAC**。

不想要了：

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1 -RemoveTask
```

---

## 第二步：日常使用

**双击 `run.bat`**。

- 会弹 UAC，点"是" —— ok-ww 必须以管理员运行，否则 `SendInput` 被系统直接丢掉（游戏是管理员权限跑的，见 README §12 UIPI）
  - 装了计划任务之后就不弹了
- **窗口会停留一会儿**：它最多等 24 秒确认 `python.exe` 真的起来了，然后打一行 `running: python.exe <pid>` 再自己关掉。这期间别急着关它
- 不带参数 = 打开 ok-ww 主界面
- `run.bat daily` = 直接跑每日任务
- `run.bat AutoCombatTask` = 跑指定任务

如果 UAC 点了"否"，或没起来，**窗口会停住并告诉你原因**，不会再一闪就没。

---

## 怎么确认真的生效

**看 ok-ww 顶栏那一行**：

```
鸣潮   2560x1440   WGC   PyDirect
```

有 `PyDirect` 就对了。

**再看日志**（`%LOCALAPPDATA%\ok-ww\data\apps\ok-ww\working\logs\ok-script.log`）末尾：

```
pydirect:left_click (1280, 720)
pydirect:left_click (2388, 1325)
```

前缀是 `pydirect:` 就对了。

如果还是 `PostMessage`，或者日志里是 `DailyTask:left_click xx (102, 253)` 这种，说明没生效 → 重跑一次 `setup.ps1`，然后**重启 ok-ww**。

---

## 三件必须知道的事

1. **跑起来后这台电脑就不能干别的了。** 它独占前台；你切到别的窗口，它就停手不发键（故意的——切走了还发，键会打到你的浏览器/聊天框里）。

2. **ok-ww 自动更新会把 `config.py` 覆盖回去。** 发现顶栏变回 `PostMessage`，重跑 `setup.ps1` 就行。

3. **每次启动要点一次 UAC。** 嫌烦就跑一次 `setup.ps1 -InstallTask`（管理员），之后不再弹；代价是系统里留两条计划任务记录（也可以 `-RemoveTask` 删掉）。

---

## 出问题对照表

| 现象 | 原因 | 怎么办 |
|---|---|---|
| 双击 `run.bat` 窗口一闪就没了，什么都没发生 | 需要管理员，UAC 要么没弹要么被拒 | 右键 `run.bat` → **以管理员身份运行**；或跑一次 `setup.ps1 -InstallTask` 免掉 UAC |
| 双击后停在 `elevation failed or was cancelled` | UAC 被你点了"否" | 重新双击，这次点"是" |
| 双击后停很久在 `waiting for python.exe ...` | ok-ww 启动慢（首次要初始化 OCR，十几秒） | 等到 24 秒上限；还没起来会打印日志路径 |
| 能截图，但点击/按键没反应 | 不是管理员 | 用 `run.bat` 启动，UAC 点"是" |
| 顶栏看不到 `PyDirect` | 配置没改或又被更新覆盖 | 重跑 `setup.ps1`，重启 ok-ww |
| 起来三十秒就自己关了 | 启动它的控制台被关，进程被连带收走 | 用 `run.bat`（里面 `start ""` 已经脱离父进程） |
| 日志报 `找不到对话框右侧按钮` | 游戏弹了个 ok-ww 不认识的窗（比如结晶波片不足） | 手动点掉弹窗再跑 |
| 日志一直 `not clickable` | 游戏不在前台 | 别切窗口，让它自己在前面跑 |
| 打字（改名/兑换码）没反应 | 中文输不进去 | 说明 `sitecustomize.py` 补丁没装上；不装也没关系，前台模式下用 `SendInput` 的 unicode 路线，见 `okww_fg/interaction.py` 的 `send_unicode` |

---

## 想恢复原样

`setup.ps1` 每次都在 `%LOCALAPPDATA%\ok-ww\_backup_<时间戳>\` 里留了：

- `config.py.orig`
- `devices.json.orig`

把这两个拷回原位，重启 ok-ww，就回到 `PostMessage` 模式（也就是会被判伪造的状态）。

---

## 更彻底的做法

这套只是**把主判据绕开**，还在同一台机器上。想让 ACE 连"有 python 在跑"都看不到，得看 `../vm/` 那套：脚本进虚拟机、游戏留物理机、采集卡看画面、树莓派当硬件键鼠。
