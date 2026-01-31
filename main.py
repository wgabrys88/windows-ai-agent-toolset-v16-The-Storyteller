"""
═══════════════════════════════════════════════════════════════════════════════
STATELESS VISUAL AGENT WITH NARRATIVE MEMORY
═══════════════════════════════════════════════════════════════════════════════

An AI controls Windows through pure visual state. Memory exists only as a report
overlay visible in screenshots—no logs, no counters, no programmatic history.

PHILOSOPHY:
    The agent has amnesia. Each decision is made fresh from a single screenshot
    showing desktop + HUD report overlay. The report is precise prose encoding
    past actions, current state, and future intent—not poetry, not bullet points.

MEMORY MECHANISM:
    HUD overlay renders the previous action's "reasoning" field as white text
    on screen. The VLM sees this report in the screenshot and writes the next
    chapter as its "reasoning" for the next action.

VISUAL TRUTH:
    If you can't see it on screen, it didn't happen. No image comparison, no
    state tracking, no retry logic. The AI sees unchanged UI and reports
    "UI unchanged after click. Will retry different coordinates."

EXECUTION FLOW:
    1. Capture screenshot with HUD report overlay
    2. VLM reads goal + screenshot → decides action + writes report
    3. Execute action, update HUD with new report
    4. Loop until VLM outputs tool: "done"

TOOLS:
    click(x,y) | move(x,y) | drag(x1,y1,x2,y2) | type(text) | scroll(dx,dy) | done()

DEPENDENCIES:
    - Windows 11 (Win32 API via ctypes)
    - LM Studio serving qwen3-vl on localhost:1234
    - Python 3.12+ (match/case, type hints)

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as w
import json
import re
import struct
import time
import urllib.request
import zlib
from dataclasses import dataclass
from enum import IntFlag
from functools import cache
from pathlib import Path
from typing import Any, Literal

MODEL_NAME = "qwen3-vl-2b-instruct-1m"
API_URL = "http://localhost:1234/v1/chat/completions"

SCREENSHOT_QUALITY = 3
SCREEN_W, SCREEN_H = {1: (1536, 864), 2: (1024, 576), 3: (512, 288)}[SCREENSHOT_QUALITY]

INPUT_DELAY_S = 0.10
DELAY_AFTER_ACTION_S = 0.50
DELAY_MOVE_HOVER_S = 2.00
DELAY_SCROLL_S = 0.30

HUD_MARGIN = 10
HUD_MAX_WIDTH = 1400
OVERLAY_REASSERT_PULSES = 2
OVERLAY_REASSERT_PAUSE_S = 0.05

DEFAULT_TASK = (
    "Open Microsoft Paint from the Start menu then use the mouse to draw a simple cat face "
    "with two circles for eyes one triangle for nose and curved line for smile then save the "
    "file as cat in the Pictures folder and close Paint when done"
)

ActionTool = Literal["click", "move", "drag", "type", "scroll", "done"]

@cache
def _dll(name: str) -> ctypes.WinDLL:
    return ctypes.WinDLL(name, use_last_error=True)

user32 = _dll("user32")
gdi32 = _dll("gdi32")
kernel32 = _dll("kernel32")

try:
    ctypes.WinDLL("Shcore", use_last_error=True).SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

class MouseEvent(IntFlag):
    MOVE = 0x0001
    ABSOLUTE = 0x8000
    LEFT_DOWN = 0x0002
    LEFT_UP = 0x0004
    WHEEL = 0x0800
    HWHEEL = 0x1000

class KeyEvent(IntFlag):
    KEYUP = 0x0002
    UNICODE = 0x0004

class WinStyle(IntFlag):
    EX_TOPMOST = 0x00000008
    EX_LAYERED = 0x00080000
    EX_TRANSPARENT = 0x00000020
    EX_NOACTIVATE = 0x08000000
    EX_TOOLWINDOW = 0x00000080
    POPUP = 0x80000000

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
WHEEL_DELTA = 120
SRCCOPY = 0x00CC0020
SW_SHOWNOACTIVATE = 4
ULW_ALPHA = 2
AC_SRC_ALPHA = 1
SWP_NOSIZE = 1
SWP_NOMOVE = 2
SWP_NOACTIVATE = 16
SWP_SHOWWINDOW = 64
HWND_TOPMOST = -1
CURSOR_SHOWING = 0x00000001
TRANSPARENT = 1
DT_LEFT = 0x00000000
DT_WORDBREAK = 0x00000010
DT_CALCRECT = 0x00000400
DI_NORMAL = 0x0003

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, WPARAM, LPARAM)
ULONG_PTR = ctypes.c_size_t

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
        ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
        ("time", w.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]

class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", w.DWORD), ("u", _INPUTunion)]

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG),
        ("biPlanes", w.WORD), ("biBitCount", w.WORD), ("biCompression", w.DWORD),
        ("biSizeImage", w.DWORD), ("biXPelsPerMeter", w.LONG),
        ("biYPelsPerMeter", w.LONG), ("biClrUsed", w.DWORD), ("biClrImportant", w.DWORD),
    ]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint * 1)]

class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD), ("flags", w.DWORD),
        ("hCursor", w.HANDLE), ("ptScreenPos", w.POINT),
    ]

class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", w.BOOL), ("xHotspot", w.DWORD), ("yHotspot", w.DWORD),
        ("hbmMask", w.HBITMAP), ("hbmColor", w.HBITMAP),
    ]

class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte),
    ]

class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint), ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", w.HINSTANCE), ("hIcon", w.HANDLE), ("hCursor", w.HANDLE),
        ("hbrBackground", w.HANDLE), ("lpszMenuName", w.LPCWSTR),
        ("lpszClassName", w.LPCWSTR),
    ]

user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, WPARAM, LPARAM]
user32.DefWindowProcW.restype = LRESULT

_SendInput = user32.SendInput
_SendInput.argtypes = (w.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_SendInput.restype = w.UINT

user32.DrawTextW.argtypes = [w.HDC, w.LPCWSTR, ctypes.c_int, ctypes.POINTER(w.RECT), w.UINT]
user32.DrawTextW.restype = ctypes.c_int

gdi32.CreateCompatibleDC.argtypes = [w.HDC]
gdi32.CreateCompatibleDC.restype = w.HDC
gdi32.CreateDIBSection.argtypes = [
    w.HDC, ctypes.POINTER(BITMAPINFO), w.UINT,
    ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD
]
gdi32.CreateDIBSection.restype = w.HBITMAP
gdi32.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
gdi32.SelectObject.restype = w.HGDIOBJ
gdi32.BitBlt.argtypes = [
    w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    w.HDC, ctypes.c_int, ctypes.c_int, w.DWORD
]
gdi32.BitBlt.restype = w.BOOL
gdi32.DeleteObject.argtypes = [w.HGDIOBJ]
gdi32.DeleteObject.restype = w.BOOL
gdi32.DeleteDC.argtypes = [w.HDC]
gdi32.DeleteDC.restype = w.BOOL
gdi32.SetBkMode.argtypes = [w.HDC, ctypes.c_int]
gdi32.SetBkMode.restype = ctypes.c_int
gdi32.SetTextColor.argtypes = [w.HDC, w.DWORD]
gdi32.SetTextColor.restype = w.DWORD
gdi32.CreateFontW.restype = w.HFONT

user32.ReleaseDC.argtypes = [w.HWND, w.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetCursorInfo.restype = w.BOOL
user32.GetIconInfo.argtypes = [w.HICON, ctypes.POINTER(ICONINFO)]
user32.GetIconInfo.restype = w.BOOL
user32.DrawIconEx.argtypes = [
    w.HDC, ctypes.c_int, ctypes.c_int, w.HICON, ctypes.c_int,
    ctypes.c_int, w.UINT, w.HBRUSH, w.UINT
]
user32.DrawIconEx.restype = w.BOOL

user32.UpdateLayeredWindow.argtypes = [
    w.HWND, w.HDC, ctypes.POINTER(w.POINT), ctypes.POINTER(w.SIZE), w.HDC,
    ctypes.POINTER(w.POINT), w.DWORD, ctypes.POINTER(BLENDFUNCTION), w.DWORD
]
user32.UpdateLayeredWindow.restype = w.BOOL

user32.SetWindowPos.argtypes = [
    w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT
]
user32.SetWindowPos.restype = w.BOOL

def get_screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

@dataclass(slots=True)
class CoordConverter:
    sw: int
    sh: int
    mw: int
    mh: int

    def norm_to_screen(self, xn: float, yn: float) -> tuple[int, int]:
        return int(xn * self.sw / 1000), int(yn * self.sh / 1000)

    def to_win32_normalized(self, x: int, y: int) -> tuple[int, int]:
        return (
            max(0, min(65535, int(x * 65535 / max(1, self.sw - 1)))),
            max(0, min(65535, int(y * 65535 / max(1, self.sh - 1)))),
        )

def _send_input(inputs: list[INPUT], *, delay_s: float = INPUT_DELAY_S) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    if _SendInput(len(inputs), arr, ctypes.sizeof(INPUT)) != len(inputs):
        raise ctypes.WinError(ctypes.get_last_error())
    if delay_s > 0:
        time.sleep(delay_s)

def mouse_move(x: int, y: int, conv: CoordConverter) -> None:
    ax, ay = conv.to_win32_normalized(x, y)
    i = INPUT(type=INPUT_MOUSE)
    i.mi = MOUSEINPUT(ax, ay, 0, MouseEvent.MOVE | MouseEvent.ABSOLUTE, 0, 0)
    _send_input([i])

def mouse_click(x: int, y: int, conv: CoordConverter) -> None:
    ax, ay = conv.to_win32_normalized(x, y)
    inputs = []
    for flag in (MouseEvent.MOVE, MouseEvent.LEFT_DOWN, MouseEvent.LEFT_UP):
        i = INPUT(type=INPUT_MOUSE)
        i.mi = MOUSEINPUT(ax, ay, 0, int(flag) | int(MouseEvent.ABSOLUTE), 0, 0)
        inputs.append(i)
    _send_input(inputs)

def mouse_drag(x1: int, y1: int, x2: int, y2: int, conv: CoordConverter) -> None:
    ax1, ay1 = conv.to_win32_normalized(x1, y1)
    ax2, ay2 = conv.to_win32_normalized(x2, y2)

    def send(flags: int, dx: int, dy: int, *, delay: float = INPUT_DELAY_S) -> None:
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(dx, dy, 0, flags, 0, 0)
        _send_input([inp], delay_s=delay)

    send(int(MouseEvent.MOVE | MouseEvent.ABSOLUTE), ax1, ay1)
    send(int(MouseEvent.LEFT_DOWN | MouseEvent.ABSOLUTE), ax1, ay1)

    for k in range(1, 15):
        t = k / 14
        dx = int(ax1 + (ax2 - ax1) * t)
        dy = int(ay1 + (ay2 - ay1) * t)
        send(int(MouseEvent.MOVE | MouseEvent.ABSOLUTE), dx, dy, delay=0.0)
        time.sleep(0.01)

    send(int(MouseEvent.LEFT_UP | MouseEvent.ABSOLUTE), ax2, ay2)

def type_text(text: str) -> None:
    if not text:
        return
    inputs = []
    for ch in text:
        b = ch.encode("utf-16le")
        for i in range(0, len(b), 2):
            cu = b[i] | (b[i + 1] << 8)
            for flags in (KeyEvent.UNICODE, KeyEvent.UNICODE | KeyEvent.KEYUP):
                inp = INPUT(type=INPUT_KEYBOARD)
                inp.ki = KEYBDINPUT(0, cu, int(flags), 0, 0)
                inputs.append(inp)
    _send_input(inputs)

def scroll(dx: float = 0.0, dy: float = 0.0) -> None:
    inputs = []
    for delta, flag in ((dy, MouseEvent.WHEEL), (dx, MouseEvent.HWHEEL)):
        if delta:
            ticks = max(1, abs(int(delta)) // 100)
            direction = 1 if delta > 0 else -1
            for _ in range(ticks):
                inp = INPUT(type=INPUT_MOUSE)
                inp.mi = MOUSEINPUT(0, 0, WHEEL_DELTA * direction, int(flag), 0, 0)
                inputs.append(inp)
    if inputs:
        _send_input(inputs)

def _capture_desktop_bgra(sw: int, sh: int) -> bytes:
    sdc = user32.GetDC(0)
    mdc = gdi32.CreateCompatibleDC(sdc)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = sw
    bmi.bmiHeader.biHeight = -sh
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32

    bits = ctypes.c_void_p()
    hbm = gdi32.CreateDIBSection(sdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    gdi32.SelectObject(mdc, hbm)
    gdi32.BitBlt(mdc, 0, 0, sw, sh, sdc, 0, 0, SRCCOPY)

    ci = CURSORINFO(cbSize=ctypes.sizeof(CURSORINFO))
    if user32.GetCursorInfo(ctypes.byref(ci)) and ci.flags & CURSOR_SHOWING:
        ii = ICONINFO()
        if user32.GetIconInfo(ci.hCursor, ctypes.byref(ii)):
            x = ci.ptScreenPos.x - ii.xHotspot
            y = ci.ptScreenPos.y - ii.yHotspot
            user32.DrawIconEx(mdc, x, y, ci.hCursor, 0, 0, 0, 0, DI_NORMAL)
            if ii.hbmMask:
                gdi32.DeleteObject(ii.hbmMask)
            if ii.hbmColor:
                gdi32.DeleteObject(ii.hbmColor)

    out = ctypes.string_at(bits, sw * sh * 4)
    user32.ReleaseDC(0, sdc)
    gdi32.DeleteDC(mdc)
    gdi32.DeleteObject(hbm)
    return out

@cache
def _nn_maps(sw: int, sh: int, dw: int, dh: int) -> tuple[list[int], list[int]]:
    xm = [((x * sw) // dw) * 4 for x in range(dw)]
    ym = [(y * sh) // dh for y in range(dh)]
    return xm, ym

def _downsample_nn_bgra(src: bytes, sw: int, sh: int, dw: int, dh: int) -> bytes:
    if (sw, sh) == (dw, dh):
        return src
    xm, ym = _nn_maps(sw, sh, dw, dh)
    src_mv = memoryview(src)
    dst = bytearray(dw * dh * 4)
    row_bytes = sw * 4
    for y, sy in enumerate(ym):
        srow = src_mv[sy * row_bytes : (sy + 1) * row_bytes]
        base = y * dw * 4
        for x, sx4 in enumerate(xm):
            di = base + x * 4
            dst[di : di + 4] = srow[sx4 : sx4 + 4]
    return bytes(dst)

def _alpha_blend_bgra(base: bytes, overlay: bytes) -> bytes:
    out = bytearray(base)
    ov = memoryview(overlay)
    for i in range(0, len(out), 4):
        oa = ov[i + 3]
        if oa:
            inv = 255 - oa
            out[i] = (ov[i] * oa + out[i] * inv + 127) // 255
            out[i + 1] = (ov[i + 1] * oa + out[i + 1] * inv + 127) // 255
            out[i + 2] = (ov[i + 2] * oa + out[i + 2] * inv + 127) // 255
    return bytes(out)

def _encode_png_rgb(bgra: bytes, width: int, height: int) -> bytes:
    raw = bytearray((width * 3 + 1) * height)
    stride_src = width * 4
    stride_dst = width * 3 + 1
    for y in range(height):
        raw[y * stride_dst] = 0
        row = bgra[y * stride_src : (y + 1) * stride_src]
        di = y * stride_dst + 1
        raw[di : di + width * 3 : 3] = row[2::4]
        raw[di + 1 : di + width * 3 : 3] = row[1::4]
        raw[di + 2 : di + width * 3 : 3] = row[0::4]
    comp = zlib.compress(bytes(raw))
    ihdr = struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", comp) + chunk(b"IEND", b"")

def _wndproc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

_wndproc_cb: WNDPROC = WNDPROC(_wndproc)

@dataclass(slots=True)
class OverlayManager:
    w: int
    h: int
    hwnd: w.HWND | None = None
    hdc: w.HDC | None = None
    hbitmap: w.HBITMAP | None = None
    bits: ctypes.c_void_p | None = None
    _font: w.HFONT | None = None
    _report: str = ""

    def __enter__(self) -> 'OverlayManager':
        hinst = kernel32.GetModuleHandleW(None)
        cls_name = "AIAgentOverlayWindow"

        wc = WNDCLASS()
        wc.lpfnWndProc = ctypes.cast(_wndproc_cb, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = cls_name

        user32.RegisterClassW(ctypes.byref(wc))

        ex = WinStyle.EX_LAYERED | WinStyle.EX_TRANSPARENT | WinStyle.EX_TOPMOST | WinStyle.EX_NOACTIVATE | WinStyle.EX_TOOLWINDOW
        self.hwnd = user32.CreateWindowExW(int(ex), cls_name, "AI Overlay", int(WinStyle.POPUP), 
                                           0, 0, self.w, self.h, 0, 0, hinst, None)

        self.hdc = gdi32.CreateCompatibleDC(0)

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = self.w
        bmi.bmiHeader.biHeight = -self.h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32

        bits = ctypes.c_void_p()
        self.hbitmap = gdi32.CreateDIBSection(0, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        self.bits = bits
        gdi32.SelectObject(self.hdc, self.hbitmap)

        self._font = gdi32.CreateFontW(-20, 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 0, 0, "Consolas")
        gdi32.SelectObject(self.hdc, self._font)
        gdi32.SetBkMode(self.hdc, TRANSPARENT)
        gdi32.SetTextColor(self.hdc, 0x00FFFFFF)

        self.render()
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        self.reassert_topmost()
        return self

    def __exit__(self, *exc) -> None:
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
        if self._font:
            gdi32.DeleteObject(self._font)
        if self.hbitmap:
            gdi32.DeleteObject(self.hbitmap)
        if self.hdc:
            gdi32.DeleteDC(self.hdc)
        user32.UnregisterClassW("AIAgentOverlayWindow", kernel32.GetModuleHandleW(None))

    def reassert_topmost(self) -> None:
        if self.hwnd:
            for _ in range(OVERLAY_REASSERT_PULSES):
                user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, 
                                   SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
                time.sleep(OVERLAY_REASSERT_PAUSE_S)

    def get_bgra_bytes(self) -> bytes:
        return ctypes.string_at(self.bits, self.w * self.h * 4) if self.bits else b""

    def set_report(self, report: str) -> None:
        self._report = report

    def render(self) -> None:
        if not self.bits:
            return
        ctypes.memset(self.bits, 0, self.w * self.h * 4)
        if self._report and self.hdc:
            x, y = HUD_MARGIN, HUD_MARGIN
            rect = w.RECT(x, y, x + HUD_MAX_WIDTH, y + 2000)
            user32.DrawTextW(self.hdc, self._report, len(self._report), ctypes.byref(rect), 
                            DT_LEFT | DT_WORDBREAK | DT_CALCRECT)
            rect.right = x + HUD_MAX_WIDTH
            user32.DrawTextW(self.hdc, self._report, len(self._report), ctypes.byref(rect), 
                            DT_LEFT | DT_WORDBREAK)
        
        bf = BLENDFUNCTION(0, 0, 255, AC_SRC_ALPHA)
        sz = w.SIZE(self.w, self.h)
        ps = w.POINT(0, 0)
        pd = w.POINT(0, 0)
        user32.UpdateLayeredWindow(self.hwnd, 0, ctypes.byref(pd), ctypes.byref(sz), 
                                  self.hdc, ctypes.byref(ps), 0, ctypes.byref(bf), ULW_ALPHA)
        self.reassert_topmost()

@dataclass(slots=True)
class ActionCommand:
    tool: ActionTool
    reasoning: str = ""
    x: float | None = None
    y: float | None = None
    text: str = ""
    dx: float = 0.0
    dy: float = 0.0
    x1: float | None = None
    y1: float | None = None
    x2: float | None = None
    y2: float | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ActionCommand":
        num = lambda v: float(v[0]) if isinstance(v, list) and v else float(v) if v else None
        return cls(
            tool=d.get("tool", ""),
            reasoning=d.get("reasoning", "").strip(),
            x=num(d.get("x")),
            y=num(d.get("y")),
            text=d.get("text", ""),
            dx=num(d.get("dx")) or 0.0,
            dy=num(d.get("dy")) or 0.0,
            x1=num(d.get("x1")),
            y1=num(d.get("y1")),
            x2=num(d.get("x2")),
            y2=num(d.get("y2")),
        )

    def validate(self) -> bool:
        match self.tool:
            case "click" | "move":
                return self.x is not None and self.y is not None and 0 <= self.x <= 1000 and 0 <= self.y <= 1000
            case "drag":
                return all(v is not None and 0 <= v <= 1000 for v in [self.x1, self.y1, self.x2, self.y2])
            case "scroll":
                return abs(self.dx) <= 10000 and abs(self.dy) <= 10000
            case "type":
                return len(self.text) <= 2000
            case "done":
                return True
            case _:
                return False

class ActionExecutor:
    def __init__(self, conv: CoordConverter):
        self.conv = conv

    def execute(self, cmd: ActionCommand) -> float:
        match cmd.tool:
            case "click":
                sx, sy = self.conv.norm_to_screen(cmd.x, cmd.y)
                mouse_click(sx, sy, self.conv)
                return DELAY_AFTER_ACTION_S
            case "move":
                sx, sy = self.conv.norm_to_screen(cmd.x, cmd.y)
                mouse_move(sx, sy, self.conv)
                return DELAY_MOVE_HOVER_S
            case "drag":
                sx1, sy1 = self.conv.norm_to_screen(cmd.x1, cmd.y1)
                sx2, sy2 = self.conv.norm_to_screen(cmd.x2, cmd.y2)
                mouse_drag(sx1, sy1, sx2, sy2, self.conv)
                return DELAY_AFTER_ACTION_S
            case "type":
                type_text(cmd.text)
                return DELAY_AFTER_ACTION_S
            case "scroll":
                scroll(cmd.dx, cmd.dy)
                return DELAY_SCROLL_S
            case _:
                return 0.0

SYSTEM_PROMPT = """You control a Windows computer. You have no memory between frames.

Your only context is white text overlay on screen. Read it. That's your past self's report. Continue from there.

Write your report as continuous prose. No labels. Describe: what you did before (read from overlay), what you see now (windows, cursor, screen state), what you'll do next (exact coordinates), backup plan if this fails.

Good report:
"Clicked Start bottom-left. Paint window center screen 768x576, canvas blank. Cursor at 768,432. Will drag circle from 400,300 to 450,350 for left eye. If no circle appears, retry with slower drag speed."

Bad report:
"The Start menu bloomed under my fingertips like morning flowers opening to the sun..."

Your report becomes overlay text for next frame. Be precise. State coordinates. Describe what changed.

Output JSON only:
{
  "tool": "click" | "move" | "drag" | "type" | "scroll" | "done",
  "x": 0-1000, "y": 0-1000,
  "x1": 0-1000, "y1": 0-1000, "x2": 0-1000, "y2": 0-1000,
  "text": "string",
  "dx": number, "dy": number,
  "reasoning": "your report (150-300 tokens, continuous prose, zero fluff)"
}

Coordinates 0-1000, top-left is 0,0. Use "done" when complete.
What happened, happened. Report it."""

def _build_messages(goal: str, curr_png: bytes) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": f"Goal: {goal}\n\nDescribe screenshot. Read white overlay (your past report). Write next action + new report."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(curr_png).decode("ascii")}}
        ]}
    ]

def call_vlm(goal: str, curr_png: bytes) -> str:
    payload = {
        "model": MODEL_NAME,
        "messages": _build_messages(goal, curr_png),
        "temperature": 0.3,
        "max_tokens": 500,
    }
    req = urllib.request.Request(API_URL, json.dumps(payload).encode("utf-8"), 
                                {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)
    content = data["choices"][0]["message"]["content"]
    return "".join(p.get("text", "") if isinstance(p, dict) else str(p) 
                  for p in content) if isinstance(content, list) else str(content)

def parse_response(resp: str) -> dict[str, Any] | None:
    s = re.sub(r"```.*?\n", "", resp, flags=re.DOTALL).strip()
    try:
        return json.loads(s) if s.startswith("{") and s.endswith("}") else None
    except Exception:
        return None

def capture_screenshot(conv: CoordConverter, ov: OverlayManager) -> bytes:
    desk = _capture_desktop_bgra(conv.sw, conv.sh)
    desk = _downsample_nn_bgra(desk, conv.sw, conv.sh, conv.mw, conv.mh)
    overlay = _downsample_nn_bgra(ov.get_bgra_bytes(), conv.sw, conv.sh, conv.mw, conv.mh)
    return _alpha_blend_bgra(desk, overlay)

def save_screenshot(path: Path, bgra: bytes) -> bytes:
    png = _encode_png_rgb(bgra, SCREEN_W, SCREEN_H)
    path.write_bytes(png)
    return png

def run_agent(goal: str, debug_dir: Path | None = None) -> None:
    sw, sh = get_screen_size()
    conv = CoordConverter(sw, sh, SCREEN_W, SCREEN_H)
    
    with OverlayManager(sw, sh) as ov:
        ex = ActionExecutor(conv)
        step = 0
        
        while True:
            step += 1
            
            bgra = capture_screenshot(conv, ov)
            curr_png = save_screenshot(debug_dir / f"step{step:03d}.png", bgra) if debug_dir else bgra
            
            resp = call_vlm(goal, curr_png)
            d = parse_response(resp)
            
            if not d:
                continue
                
            cmd = ActionCommand.from_dict(d)
            
            if not cmd.validate():
                continue
            
            if cmd.tool == "done":
                break
            
            delay = ex.execute(cmd)
            time.sleep(delay)
            
            ov.set_report(cmd.reasoning or "No report provided.")
            ov.render()
            time.sleep(0.2)

def main() -> None:
    print(f"Default: {DEFAULT_TASK}")
    choice = input("ENTER=default, 'n'=custom: ").strip().lower()
    goal = input("Task: ").strip() if choice == "n" else DEFAULT_TASK
    time.sleep(5)
    if not goal:
        return
    
    debug_dir = Path("dump") / f"run_{time.strftime('%Y%m%d_%H%M%S')}"
    debug_dir.mkdir(parents=True, exist_ok=True)
    
    run_agent(goal, debug_dir)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
