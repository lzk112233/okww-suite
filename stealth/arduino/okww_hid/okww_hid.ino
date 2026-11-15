/*
 * okww_hid.ino — USB HID 桥固件（RP2040 / ATmega32u4）
 *
 * 作用：把 PC 发来的串口指令变成真实的 USB 键鼠报告。
 * 游戏侧看到的就是一个普通 USB 复合设备，没有 SendInput、没有 injected 标志、
 * 没有目标进程句柄、没有截图调用。
 *
 * 协议（与 hid_bridge.py 对应，115200 8N1）
 *   AA | CMD | LEN | PAYLOAD(LEN) | XOR(head,cmd,len,payload)
 *   0x01 MOUSE   int16 dx | int16 dy | int8 wheel | uint8 buttons
 *   0x02 KEY     uint8 modifier | uint8 k0..k5
 *   0x03 PING    -> 回 0x83 'O' 'K'
 *   0x04 RELEASE 释放所有键位
 *
 * 编译：
 *   RP2040 : Arduino IDE + "Raspberry Pi Pico/RP2040" core（Adafruit TinyUSB）
 *    Leonardo / Pro Micro : Arduino AVR core，直接选板子即可
 *
 * 想改 VID/PID/产品名（避免设备管理器里出现奇怪名字）：
 *   RP2040 在 boards.txt 里加 build.usb_manufacturer / build.usb_product，
 *   或在 -DUSB_MANUFACTURER="Generic" -DUSB_PRODUCT="USB Composite Device"
 */

#include <Arduino.h>

#define CMD_MOUSE   0x01
#define CMD_KEY     0x02
#define CMD_PING    0x03
#define CMD_RELEASE 0x04
#define HEAD_BYTE   0xAA

/* ─────────────── USB HID 抽象层 ─────────────── */

#if defined(ARDUINO_ARCH_RP2040) || defined(ARDUINO_ARCH_MBED_RP2040)

#include "Adafruit_TinyUSB.h"

static uint8_t const desc_hid_report[] = {
  TUD_HID_REPORT_DESC_KEYBOARD(HID_REPORT_ID(1)),
  TUD_HID_REPORT_DESC_MOUSE(HID_REPORT_ID(2))
};

Adafruit_USBD_HID usb_hid;

static inline void hid_begin() {
  usb_hid.setReportDescriptor(desc_hid_report, sizeof(desc_hid_report));
  usb_hid.setPollInterval(1);          // 1ms 轮询，和真键鼠一致
  usb_hid.setStringDescriptor("USB Composite Device");
  usb_hid.begin();
  while (!TinyUSBDevice.mounted()) delay(10);
}

static uint8_t g_btn = 0;
static uint8_t g_mod = 0;
static uint8_t g_keys[6] = {0, 0, 0, 0, 0, 0};

static inline void hid_mouse_report(uint8_t buttons, int8_t dx, int8_t dy, int8_t wheel) {
  g_btn = buttons;
  if (!usb_hid.ready()) return;
  usb_hid.mouseReport(2, buttons, dx, dy, wheel, 0);
}

static inline void hid_key_report(uint8_t mod, const uint8_t k[6]) {
  g_mod = mod;
  memcpy(g_keys, k, 6);
  if (!usb_hid.ready()) return;
  uint8_t tmp[6];
  memcpy(tmp, k, 6);
  usb_hid.keyboardReport(1, mod, tmp);
}

static inline void hid_release_all() {
  hid_mouse_report(0, 0, 0, 0);
  uint8_t z[6] = {0, 0, 0, 0, 0, 0};
  hid_key_report(0, z);
}

#else   /* ── ATmega32u4：用 Arduino 内置 HID ── */

#include <HID.h>
#include <Mouse.h>
#include <Keyboard.h>

static uint8_t g_btn = 0;
static uint8_t g_mod = 0;
static uint8_t g_keys[6] = {0, 0, 0, 0, 0, 0};
static uint8_t s_mod = 0;
static uint8_t s_keys[6] = {0, 0, 0, 0, 0, 0};

/* HID modifier bit -> Arduino Keyboard 虚拟键码 */
static const uint8_t MOD2KEY[8] = {
  0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87
};

static inline void hid_begin() {
  Mouse.begin();
  Keyboard.begin();
}

static inline void hid_mouse_report(uint8_t buttons, int8_t dx, int8_t dy, int8_t wheel) {
  uint8_t changed = buttons ^ g_btn;
  // 先把改变之外的位移发掉，保证按下/抬起与移动同帧送达
  if (dx || dy || wheel) Mouse.move(dx, dy, wheel);
  if (changed & 0x01) { if (buttons & 0x01) Mouse.press(MOUSE_LEFT);   else Mouse.release(MOUSE_LEFT); }
  if (changed & 0x02) { if (buttons & 0x02) Mouse.press(MOUSE_RIGHT);  else Mouse.release(MOUSE_RIGHT); }
  if (changed & 0x04) { if (buttons & 0x04) Mouse.press(MOUSE_MIDDLE); else Mouse.release(MOUSE_MIDDLE); }
  g_btn = buttons;
}

static inline void hid_key_report(uint8_t mod, const uint8_t k[6]) {
  /* 修饰键 diff */
  for (uint8_t i = 0; i < 8; i++) {
    bool was = s_mod & (1 << i), now = mod & (1 << i);
    if (was != now) {
      if (now) Keyboard.press(MOD2KEY[i]);
      else     Keyboard.release(MOD2KEY[i]);
    }
  }
  /* 普通键 diff */
  for (uint8_t i = 0; i < 6; i++) {
    uint8_t old = s_keys[i];
    bool still = false;
    for (uint8_t j = 0; j < 6; j++) if (k[j] == old) still = true;
    if (old && !still) Keyboard.release(old);
  }
  for (uint8_t i = 0; i < 6; i++) {
    uint8_t nk = k[i];
    if (!nk) continue;
    bool had = false;
    for (uint8_t j = 0; j < 6; j++) if (s_keys[j] == nk) had = true;
    if (!had) Keyboard.press(nk);
  }
  memcpy(s_keys, k, 6);
  s_mod = mod;
  memcpy(g_keys, k, 6);
  g_mod = mod;
}

static inline void hid_release_all() {
  for (uint8_t i = 0; i < 8; i++) Keyboard.release(MOD2KEY[i]);
  for (uint8_t i = 0; i < 6; i++) if (s_keys[i]) Keyboard.release(s_keys[i]);
  memset(s_keys, 0, 6);
  s_mod = 0;
  g_mod = 0;
  Mouse.release(MOUSE_LEFT); Mouse.release(MOUSE_RIGHT); Mouse.release(MOUSE_MIDDLE);
  g_btn = 0;
}

#endif

/* ─────────────── 帧解析状态机 ─────────────── */

enum ParseState { S_HEAD, S_CMD, S_LEN, S_BODY, S_CHK };
static ParseState st = S_HEAD;
static uint8_t cmd = 0, len = 0, idx = 0, chk = 0;
static uint8_t body[16];

static inline void send_ok() {
  Serial.write(0x83);
  Serial.write('O');
  Serial.write('K');
}

static void handle_frame() {
  if (cmd == CMD_MOUSE && len == 6) {
    int16_t dx = (int16_t)(body[0] | (body[1] << 8));
    int16_t dy = (int16_t)(body[2] | (body[3] << 8));
    int8_t  wh = (int8_t)body[4];
    uint8_t bt = body[5];
    /* 单次报告最多 ±127，超出部分按 1ms 节奏补齐（保持与手势移动一致） */
    while (dx || dy) {
      int8_t sx = dx > 127 ? 127 : (dx < -127 ? -127 : (int8_t)dx);
      int8_t sy = dy > 127 ? 127 : (dy < -127 ? -127 : (int8_t)dy);
      hid_mouse_report(bt, sx, sy, wh);
      wh = 0;
      dx -= sx; dy -= sy;
      if (dx || dy) delay(1);
    }
    if (wh) hid_mouse_report(bt, 0, 0, wh);
  } else if (cmd == CMD_KEY && len == 7) {
    hid_key_report(body[0], &body[1]);
  } else if (cmd == CMD_PING) {
    send_ok();
  } else if (cmd == CMD_RELEASE) {
    hid_release_all();
  }
}

void setup() {
  Serial.begin(115200);
  hid_begin();
  hid_release_all();
  /* 开机自检：让上位机知道桥在线 */
  delay(120);
  send_ok();
}

void loop() {
  while (Serial.available() > 0) {
    uint8_t b = (uint8_t)Serial.read();
    switch (st) {
      case S_HEAD:
        if (b == HEAD_BYTE) { chk = b; st = S_CMD; }
        break;
      case S_CMD:
        cmd = b; chk ^= b; st = S_LEN;
        break;
      case S_LEN:
        len = b; chk ^= b; idx = 0;
        if (len > sizeof(body)) { st = S_HEAD; }
        else st = (len == 0) ? S_CHK : S_BODY;
        break;
      case S_BODY:
        body[idx++] = b; chk ^= b;
        if (idx >= len) st = S_CHK;
        break;
      case S_CHK:
        if (b == chk) handle_frame();
        st = S_HEAD;
        break;
    }
  }
}
