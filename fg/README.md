# okww-fg

ok-ww 的 `PostMessage` 输入路径会让游戏**收到一条伪造的「我被激活了」消息**。
这个项目把那一条消息换成真的把窗口切到前台，并且让键鼠从真实输入栈走。

不改 ok-ww 一行代码，靠 `PYTHONPATH` + `sitecustomize.py` 注入。

---

## 1. 判据：游戏侧三行代码

真实窗口激活由 `win32k!xxxActivateWindow` 以 **SendMessage 语义**同步投递，
只在该窗口真的被激活时发生。`GetForegroundWindow()` 读的是内核里的
`gpqForeground`，用户态伪造不了。

所以游戏只需要：

```c
case WM_ACTIVATE:
    if (GetForegroundWindow() != hwnd)
        report_fake_activation();   // 举手了
    break;
```

ok-ww 的做法（`post_message.py:144`）：

```python
self.post(win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0, hwnd=hwnd)
```

**每按一次键、每点一次鼠标都发**，`update_mouse_pos()` 里更是无条件发
（`post_message.py:203-204`）。`try_activate()` 在 base 与 target 两个 hwnd
不一样时还会一次发两条。挂在那儿不动也在发。

---

## 2. 实测证据

`python tools\probe_activation.py` 建一个真窗口，在窗口过程里对每条
`WM_ACTIVATE` 反问 `GetForegroundWindow()` 是不是自己：

```
probe hwnd = 133594  title='okww-fg WM_ACTIVATE probe'
foreground at start = 855396 ('README.md - Notepad')

  force_foreground -> ok=True after=133594 elapsed=0.0004

================================================================
按阶段统计
================================================================
  setup                    共   1 条 | 被判伪造   1 (100%) | 真的   0
  A_postmessage            共  20 条 | 被判伪造  20 (100%) | 真的   0
  C_postmessage_when_fg    共   6 条 | 被判伪造   1 ( 16%) | 真的   5

总计 WM_ACTIVATE 27 条，其中 GetForegroundWindow() != 自己 的有 22 条
```

| 阶段 | 做法 | 条数 | 判伪造 |
|---|---|---|---|
| A | `PostMessage(WM_ACTIVATE, WA_ACTIVE)` × 20（ok-ww 原样） | 20 | **20 (100%)** |
| B | `force_foreground(hwnd)` | — | 真切过去了 |
| C | 窗口真在前台时再 `PostMessage` 同样消息 | 6 | 1 (16%) |

**A 阶段 20/20 全中，这就是 ACE 眼里的铁证。** C 阶段说明判据不是
「有没有收到 `WM_ACTIVATE`」，而是「收到时 `GetForegroundWindow()` 是不是自己」——
所以把伪造换成真切换，这一关就过了。

> 本机实测有个前提：`SPI_GETFOREGROUNDLOCKTIMEOUT = 2147483647`（0x7FFFFFFF，
> 等于「无限」）。这意味着任何非前台进程调 `SetForegroundWindow` 都会被前台锁
> 挡回去。**ok-ww 当初改用 PostMessage 假装激活，大概率就是因为它真的切不过去。**
> `force_foreground(..., unlock=True)` 会临时把该值设 0、切完恢复。

---

## 3. 它是怎么做的

```
ok-ww 调 post_message.py
        │
        ▼
  _fg_post(message, wParam, lParam, hwnd)      ← 被替换的 post()
        │
        ├── WM_ACTIVATE / WM_NCACTIVATE /
        │   WM_SETFOCUS / WM_KILLFOCUS
        │        └──> ensure_foreground(hwnd)  真 SetForegroundWindow，不发消息
        │
        ├── WM_MOUSEMOVE  ──> client_to_screen ──> sink.move_to(x, y)
        ├── WM_*BUTTON*   ───────────────────────> sink.button(mask, down)
        ├── WM_XBUTTON*   ──> wParam 高字判 XBUTTON1/2 ──> sink.button
        ├── WM_MOUSEWHEEL ──> delta/120 ─────────> sink.wheel(clicks)
        ├── WM_KEYDOWN/UP ──> vk&0xFF ───────────> sink.key(vk, down)
        ├── WM_CHAR       ───────────────────────> sink.char(code)
        └── 其余            ─────────────────────> 原样 PostMessage
```

设计上的四个点：

1. **打方法补丁，不换类名。** ok-ww 可能已经 `from ... import PostMessageInteraction`，
   换类名会失效。所以直接改 `PostMessageInteraction.post / activate / deactivate /
   try_activate / send_key_down / send_key_up`。
2. **`min_raise_gap = 0.60`。** 两次真前台切换至少隔 600ms，否则 ok-ww 高频动作会把
   屏幕切得像抽风。
3. **连续移动走快速分片。** 60ms 内再次 `move_to` 或距离 ≤6px 时不排贝塞尔曲线——
   否则 swipe 每秒 100 次 move、每次规划 0.1~0.3s，滑一次要十几秒。
4. **`send_key_down` / `send_key_up` 也一起换。** 这两个不走 `post()`，
   它们直接调 `post(WM_KEYDOWN, ...)`，所以只补 `post()` 会漏掉键盘激活。

---

## 4. 上手

```powershell
# 独立跑（会打印 install 结果）
$env:PYTHONPATH = "D:\ai\dfy\okww-fg;D:\ai\dfy\okww-stealth"
cd C:\Users\<你>\AppData\Local\ok-ww\data\apps\ok-ww\working
python -c "import okww_fg, json; print(json.dumps(okww_fg.install(quiet=False), indent=2))"
```

实测输出：

```json
{
  "ok": true,
  "patched": ["post", "activate", "deactivate", "try_activate",
              "send_key_down", "send_key_up"],
  "sink": "sendinput",
  "foreground": true,
  "humanized": true,
  "foreground_now": 855396
}
```

**给 ok-ww 本体用**：把 `D:\ai\dfy\okww-fg` 放进 `PYTHONPATH` 就行，
`sitecustomize.py` 会自动 `install()`。ok-ww 的 `.pyappify-shortcut.py`
会 pop 掉 `PYTHONPATH`，所以要么改 app.json 的 `PYTHONPATH` 字段，
要么让 `sitecustomize.py` 落在 `python\Lib\site-packages\` 里。

### 环境变量

| 变量 | 作用 |
|---|---|
| `OKWW_FG=0` | 完全关闭 |
| `OKWW_FG_FOREGROUND=0` | 保留 sink（真输入），但不切前台（窗口留在后台） |
| `OKWW_FG_BACKEND=sendinput\|interception\|hid` | 选输入后端 |
| `OKWW_STEALTH_DIR=<path>` | okww-stealth 路径（默认自动找） |
| `OKWW_FG_VERBOSE=1` | 打印细节 |

---

## 5. 文件

| 文件 | 行数 | 干什么 |
|---|---|---|
| `okww_fg\foreground.py` | 299 | 真前台：`force_foreground()`、`AttachThreadInput`、`_SPI` 前台锁、按 ALT 解锁、轮询验证 |
| `okww_fg\interaction.py` | ~430 | 补丁主体：`_fg_post` 分派、`InputSink`、`_FgState`、`install()` |
| `okww_fg\__init__.py` | 30 | re-export `install` / `status` / `foreground` |
| `sitecustomize.py` | 60 | PYTHONPATH 自动注入，异常全吞只记 `OKWW_FG_STATUS` |
| `tools\probe_activation.py` | 230 | 证据工具，输出上面那张对照表 |

---

## 6. 三种输入后端

`sink` 拿的是 `okww-stealth` 的 `human_input`，所以直接继承那套人类化轨迹
（贝塞尔 + Fitts + 抖动）。按 `OKWW_FG_BACKEND` 或自动顺序挑：

| backend | 从哪来 | 特征 |
|---|---|---|
| `hid` | 串口 → 树莓派/Arduino HID | 真 USB 报告，无 `LLMHF_INJECTED` |
| `interception` | Interception 驱动 | 内核级注入，绕过用户态钩子 |
| `sendinput` | `SendInput` / `mouse_event` | **带 `LLMHF_INJECTED` 标志**，最容易被抓 |

拿不到 `okww-stealth` 时退回内置 `_RawSendInput`（直接 `mouse_event`）。

---

## 7. 和另外三个项目的关系

```
okww-stealth    软件层抹除（进程名/日志/网络/窗口标题）
okww-fg         本项：把伪造激活换成真前台，输入改走真实输入栈
okww-vm         双机/虚拟机：脚本进 VM，游戏留物理机
okww-rename     单机改名（进程名/注册表/安装路径）
```

四个可以叠。但优先级：**`okww-fg` > `okww-stealth` > `okww-rename` > `okww-vm`**，
因为伪造激活是零成本、零误判的判据，先把它消掉最划算。

---

## 8. 改不掉什么

1. **`SendInput` 的 `LLMHF_INJECTED` 标志还在**（除非上 `hid` 后端）。
   `ACE-BASE.sys` 导入了 `HIDPARSE.SYS`，双路比对时这一路对不上。
2. **窗口标题还是 `ok-ww`**（用 `OKWW_FG` 不管这个，要 `okww-stealth` 的 `qtcloak`）。
3. **真前台 = 屏幕真的被切走。** 边玩游戏边干别的会被抢焦点。这是行为层的
   副作用，不是检测层的问题。
4. **`AntiBot` 行为模型**照样跑。这个项目只管「消息真不真」，不管「操作像不像人」。
5. **每次切换 0.4~12ms**（实测 `elapsed=0.0004` 是因为窗口已经在边上了，
   冷切一般 8~15ms）。ok-ww 每次动作都切的话开销可观，所以才有 `min_raise_gap`。

---

## 9. 自检清单

- [ ] `python -m py_compile` 五个文件全过
- [ ] `python tools\probe_activation.py` 里 A 阶段应为 20/20 判伪造
- [ ] `install()` 返回 `ok: true`，`patched` 六个方法齐全
- [ ] 开游戏跑一段，用 Process Explorer 看 `SGuard64.exe` 无异常句柄增长
- [ ] 确认 `SPI_GETFOREGROUNDLOCKTIMEOUT`：`python -c "import okww_fg.foreground as f; print(f.get_lock_timeout())"`
- [ ] `python tools\e2e_focus.py` 输出 `('text', 'ABC')` + PASS
- [ ] `python tools\e2e_patched.py`：补丁轮 `伪造=0`、`WM_ACTIVATE>0`、`EDIT文字='xyz'`

---

## 10. 「就在焦点上跑」模式做了什么（2026-11-14 实测）

用户选了这条路：**接受抢焦点**，换来不用采集卡、不用第二台机器。

### 10.1 六处「切不过去就不发」

以前 `_fg_post` 里 `st.ensure_foreground(hwnd)` 的返回值全被丢弃 —— 抢不到焦点照样
用 SendInput 发键。**SendInput 是全局投递**，抢不到就意味着键打到别的窗口去（聊天框、
浏览器地址栏），比不操作更糟。现在六处统一：

```python
if not st.ensure_foreground(hwnd):
    return
```

| 消息 | 处理 |
|---|---|
| `WM_MOUSEMOVE` | 不抢焦点（抢了反而抽风），只在已经在前台时才移动 |
| `WM_LBUTTONDOWN` / `R` / `M` / `X` | 抢不到 → 丢弃 |
| `WM_MOUSEWHEEL` | 抢不到 → 丢弃 |
| `WM_KEYDOWN` / `WM_KEYUP` | 抢不到 → 丢弃 |
| `WM_CHAR` | 抢不到 → 丢弃 |

### 10.2 `WM_CHAR` 真正能打字了

原来 `InputSink.char()` 两条路都堵死：`human_input.SendInputBackend` **没有
`unicode` 方法**；退化路 `VkKeyScanW` 只能处理当前键盘布局里有的字符（中文、符号
一律返回 -1），失败就静默什么都不发。ok-ww 的 `input_text`（输名字、兑换码）全废。

现在按顺序走三级：

1. `send_unicode(code)` —— 自己封的 `SendInput(KEYEVENTF_UNICODE)`，不挑键盘布局，
   补充平面自动拆代理对
2. 后端的 `unicode`（HID 桥那边有）
3. 最后才退 `VkKeyScanW`

### 10.3 实测对照（`tools\e2e_patched.py`，真 ok-ww 的 `PostMessageInteraction`）

```
阶段                抢到前台   耗时ms   WM_ACTIVATE  伪造   EDIT文字
原版(PostMessage)    True      0.0      5            1      'xyz'
补丁(真前台)         True      30.5     1            0      'xyz'
```

补丁轮：**真切到前台 30.5ms，窗口收到的那条 `WM_ACTIVATE` 里 `GetForegroundWindow()`
是它自己 —— 0 条伪造**。字符"xyz"准确落进目标 EDIT。

另跑 `tools\e2e_focus.py`：从 Edge 抢前台 **54ms**，`('text', 'ABC')`。

### 10.4 这模式的实际体验

- **开着 okww 时，你切到别的窗口，它会在下一次动作前把你切回来。** 写代码、看视频
  都会被抢。这是设计如此，不是 bug。
- `min_raise_gap = 0.60`：0.6 秒内不重复抢，避免疯狂切屏。
- 一旦抢到，后续 `ensure_foreground` 直接短路（前台已经是它），不再有开销。
- **独占全屏（Exclusive Full Screen）无效** —— DWM 会绕过一切 `SetForegroundWindow`。
  鸣潮设成「窗口化」或「无边框窗口」才有效。
- 抢不到时的症状：能截图（BitBlt 走的是窗口句柄，不需要前台），但点击/按键全丢。
  这是**故意的**：宁可不动，也不要把键洒到别的窗口。
