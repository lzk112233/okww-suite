# 最简单用法

## 它解决什么

ok-ww 原来用 `PostMessage` 往游戏窗口"塞"假键鼠消息，而且**每发一条之前都先补一条伪造的窗口激活**（`post_message.py:144/147`，`WM_ACTIVATE`）。

游戏只要在自己收到 `WM_ACTIVATE` 时问一句 `GetForegroundWindow() == 我?` —— 不是，就是伪造的。这就是"开着不操作也封"的原因。

这套东西把它换成 `PyDirect`：**真的把游戏切到前台，用 `SendInput` 发真键鼠**，一条窗口消息都不发。

---

## 第一步：装一次（只做一次）

前提：ok-ww 已经装好，能正常打开（默认在 `%LOCALAPPDATA%\ok-ww`）。

打开 PowerShell，进到 `fg` 目录，跑：

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

---

## 第二步：日常使用

**双击 `run.bat`**。

- 会弹 UAC，点"是" —— ok-ww 必须以管理员运行，否则 `SendInput` 被系统直接丢掉（游戏是管理员权限跑的，见 README §12 UIPI）
- 不带参数 = 打开 ok-ww 主界面
- `run.bat daily` = 直接跑每日任务
- `run.bat AutoCombatTask` = 跑指定任务

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

3. **每次启动要点一次 UAC。** 不想点可以建个"以最高权限运行"的计划任务，但那会在系统里留一条计划任务记录，反而多一个特征，不建议。

---

## 出问题对照表

| 现象 | 原因 | 怎么办 |
|---|---|---|
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
