"""Lyric - 在 Windows 任务栏上显示当前播放歌曲的歌词。

歌曲信息来自 Windows SMTC（系统媒体控制），凡是会出现在系统「媒体面板」里的播放器都能识别：
QQ 音乐、网易云音乐、Spotify、浏览器、抖音等。歌词来自网易云音乐公开 API，
按「歌名 + 歌手」搜索，缓存在 %LOCALAPPDATA%\\taskbar-lyric。

    pythonw lyric.py            运行（无控制台窗口）
    python  lyric.py --selftest

托盘图标：左键打开设置，右键可显示/隐藏歌词、开机自启、退出。
歌词栏：左键拖动移动位置，右键菜单重置位置/退出。
"""

import asyncio
import bisect
import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path

try:
    import pystray
    from PIL import Image, ImageDraw
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _SessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as _Status,
    )
except ImportError:
    sys.exit("缺少依赖，请先运行:  pip install -r requirements.txt")


class _DevNull:
    """PyInstaller 的 --noconsole 模式下 sys.stdout / sys.stderr 是 None。"""

    def write(self, *_):
        pass

    def flush(self):
        pass

    def reconfigure(self, **_):
        pass


for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, _DevNull())
    else:
        try:
            getattr(sys, _name).reconfigure(errors="replace")
        except Exception:
            pass


# ---- 默认值（可在设置窗口里改，改完存到 config.json） ----------------------
DEFAULTS = {
    "font_size": 16,
    "alpha": 0.82,
    "width": 0.6,   # 占屏幕宽度的比例
    "dock": True,   # 镶嵌进任务栏；False = 当普通置顶悬浮条
    "height": 30,   # 实测 Win11 任务栏元素都是 30px 高
    "radius": 10,   # 实测搜索框圆角约 10px
    "lock": False,  # 锁定位置后拖不动
}
FONT_FAMILY = "Microsoft YaHei UI"
POLL_SECONDS = 0.4  # 轮询播放器的间隔
TICK_MS = 100
# ---------------------------------------------------------------------------

APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "taskbar-lyric"
CACHE_FILE = APP_DIR / "cache.json"
CONFIG_FILE = APP_DIR / "config.json"
BG = "#0d0d12"        # 常态底色
BG_HOVER = "#2b2b34"  # 悬停变亮，模仿搜索框的 hover 反馈
FG = "#f2f2f7"

POST = queue.Queue()  # 托盘线程 -> 主线程的动作队列


# ---- json 小工具 ----------------------------------------------------------

def _load_json(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def _save_json(path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # ponytail: 非原子写，读的人（另一个程序每 100ms 轮询）会读到空文件或半截 JSON，
        # 由读取方的 guard 兜住（见 Overlay._reload_if_changed）。
        # 要连「写到一半崩了」也避免，再改成 写临时文件 + os.replace——
        # 注意 Windows 下对方开着文件时替换会失败，那次写入不能就这么丢掉。
        path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    except OSError:
        pass


# ---- 歌词 -----------------------------------------------------------------

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
_STAMP = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")
_OFFSET = re.compile(r"\[offset:\s*([+-]?\d+)\]", re.I)
# 网易云的歌词正文里混着「作词 : X」这类制作人名单，丢掉
_CREDIT = re.compile(
    r"^(作词|作曲|编曲|制作人|出品|监制|混音|母带|录音|和声|吉他|贝斯|鼓|键盘|"
    r"弦乐|统筹|策划|封面|设计|文案|发行|OP|SP|词|曲)\s*[:：]"
)


def _api(url):
    request = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Referer": "https://music.163.com/"}
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)


def _search(query):
    url = "https://music.163.com/api/search/get?s=%s&type=1&limit=5" % urllib.parse.quote(query)
    return (_api(url).get("result") or {}).get("songs") or []


def _fetch_lrc(song_id):
    url = "https://music.163.com/api/song/lyric?id=%s&lv=1&kv=1&tv=-1" % song_id
    return (_api(url).get("lrc") or {}).get("lyric") or ""


def fetch_lyrics(title, artist, cache):
    """返回 LRC 文本，失败返回 ''。会写入 ``cache``（key -> LRC）。"""
    key = "%s|%s" % (title, artist)
    if key in cache:
        return cache[key]
    lrc = ""
    queries = ["%s %s" % (title, artist)] if artist else []
    queries.append(title)
    for query in queries:
        songs = _search(query.strip())
        if not songs:
            continue
        # 优先取歌名完全一致的，否则取第一条
        best = next((s for s in songs if (s.get("name") or "").lower() == title.lower()), songs[0])
        lrc = _fetch_lrc(best["id"])
        if lrc:
            break
    cache[key] = lrc
    _save_json(CACHE_FILE, cache)
    return lrc


def parse_lrc(text):
    """LRC -> (times, lines)，按时间排序。times[i] 是秒（float）。"""
    offset = _OFFSET.search(text)
    # ponytail: offset 符号按最直白的读法处理，遇到反过来的文件改这里一个字符
    shift = int(offset.group(1)) / 1000.0 if offset else 0.0
    pairs = []
    for raw in text.splitlines():
        stamps = _STAMP.findall(raw)
        if not stamps:
            continue
        line = _STAMP.sub("", raw).strip()
        if not line or _CREDIT.match(line):
            continue
        for minutes, seconds in stamps:
            # "12:34"（帧）当成 12.34 秒，只有极少数文件这么写，够用
            at = int(minutes) * 60 + float(seconds.replace(":", ".")) + shift
            pairs.append((at, line))
    pairs.sort()
    return [t for t, _ in pairs], [w for _, w in pairs]


def line_at(times, now):
    """返回 ``now`` 秒时对应的歌词下标，第一句之前返回 -1。"""
    return bisect.bisect_right(times, now) - 1


# ---- 当前播放（Windows SMTC） ---------------------------------------------

async def _sample(manager):
    session = manager.get_current_session()
    # 「当前会话」经常是浏览器里一个没在播的标签页，优先选真正在播放的那个
    if session is None or session.get_playback_info().playback_status != _Status.PLAYING:
        playing = [
            s for s in manager.get_sessions()
            if s.get_playback_info().playback_status == _Status.PLAYING
        ]
        if playing:
            session = playing[0]
    if session is None:
        return {"title": "", "artist": "", "playing": False, "pos": 0.0, "stamp": 0.0, "error": ""}
    props = await session.try_get_media_properties_async()
    timeline = session.get_timeline_properties()
    return {
        "title": (props.title or "").strip(),
        "artist": (props.artist or "").strip(),
        "playing": session.get_playback_info().playback_status == _Status.PLAYING,
        "pos": timeline.position.total_seconds(),
        "stamp": time.monotonic(),
        "error": "",
    }


async def _pump(state):
    try:
        manager = await _SessionManager.request_async()
    except Exception as exc:
        state["error"] = "SMTC 不可用: %s" % exc
        return
    while True:
        try:
            state.update(await _sample(manager))
        except Exception as exc:  # 播放器可能读到一半就退出了
            state["error"] = "%s: %s" % (type(exc).__name__, exc)
        await asyncio.sleep(POLL_SECONDS)


# ---- 歌词缓存/后台抓取 -----------------------------------------------------

class Lyrics:
    """在后台线程抓歌词，结果整体替换，避免读到半成品。"""

    def __init__(self, cache):
        self.cache = cache
        self._cur = ("", [], [])  # (key, times, lines)
        self._pending = None

    def current(self, key):
        return (self._cur[1], self._cur[2]) if self._cur[0] == key else ([], [])

    def ensure(self, key, title, artist):
        if self._cur[0] == key or self._pending == key:
            return
        self._pending = key
        threading.Thread(target=self._work, args=(key, title, artist), daemon=True).start()

    def _work(self, key, title, artist):
        try:
            times, lines = parse_lrc(fetch_lyrics(title, artist, self.cache))
        except Exception as exc:
            print("歌词获取失败:", exc, file=sys.stderr)
            times, lines = [], []
        if self._pending == key:  # 用户已经切歌了，丢弃过期结果
            self._cur = (key, times, lines)
            self._pending = None


# ---- 任务栏嵌入（Win32 SetParent） -----------------------------------------
# Win11 的任务栏把内容画在 XAML 层里，但它仍然收外来子窗口——实测 SetParent 到
# Shell_TrayWnd 之后能正常显示，而且会跟着任务栏一起自动隐藏。

_u32 = ctypes.windll.user32
_GWL_STYLE, _GWL_EXSTYLE = -16, -20
_WS_CHILD, _WS_POPUP = 0x40000000, 0x80000000
_WS_EX_TOPMOST = 0x00000008
_SWP_NOACTIVATE = 0x0010

_u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_u32.FindWindowW.restype = wintypes.HWND
_u32.IsWindow.argtypes = [wintypes.HWND]
_u32.IsWindow.restype = wintypes.BOOL
_u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.GetWindowLongW.restype = ctypes.c_long
_u32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
_u32.SetWindowLongW.restype = ctypes.c_long
_u32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
_u32.SetParent.restype = wintypes.HWND
_u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, ctypes.c_uint]
_u32.SetWindowPos.restype = wintypes.BOOL


def find_taskbar():
    """任务栏窗口句柄，找不到返回 0。"""
    return _u32.FindWindowW("Shell_TrayWnd", None) or 0


def taskbar_size(tray):
    rect = wintypes.RECT()
    _u32.GetClientRect(tray, ctypes.byref(rect))
    return rect.right, rect.bottom


def set_child_pos(hwnd, x, y, w, h):
    """SetParent 之后 Tk 会把紧跟的那次 geometry 盖掉（窗口停在任务栏 0,0）。

    所以这里在 geometry() 之后再直接压一次 OS 位置：Tk 的记录已经被 geometry()
    同步成同一个值，两边一致，之后就不会互相覆盖。
    """
    _u32.SetWindowPos(hwnd, None, int(x), int(y), int(w), int(h), _SWP_NOACTIVATE)


def _reset_layered(hwnd, ex):
    """把 WS_EX_LAYERED 摘掉再加回来。

    SetParent 之后 DWM 就不再合成这个窗口了：窗口、位置、Z 序、WS_EX_LAYERED
    全都对，但一个像素都不画（实测重新 SetLayeredWindowAttributes、RedrawWindow、
    SWP_FRAMECHANGED 都救不回来）。只有摘掉再加回来才恢复。
    调用方之后必须再走一次 ensure_layered —— 刚加回分层位的窗口是不透明的，
    得重新 SetLayeredWindowAttributes 才画得出来。
    """
    _u32.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex & ~_WS_EX_LAYERED)
    _u32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                      _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOZORDER | _SWP_FRAMECHANGED)
    _u32.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex)
    _u32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                      _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOZORDER | _SWP_FRAMECHANGED)


def dock_into(hwnd, tray):
    """把窗口改成任务栏的子窗口（去掉 popup / topmost 样式）。"""
    style = _u32.GetWindowLongW(hwnd, _GWL_STYLE)
    _u32.SetWindowLongW(hwnd, _GWL_STYLE, (style & ~_WS_POPUP) | _WS_CHILD)
    ex = _u32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & ~_WS_EX_TOPMOST
    _u32.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex)
    _u32.SetParent(hwnd, tray)
    _reset_layered(hwnd, ex)


def undock_from(hwnd):
    _u32.SetParent(hwnd, None)
    style = _u32.GetWindowLongW(hwnd, _GWL_STYLE)
    _u32.SetWindowLongW(hwnd, _GWL_STYLE, (style & ~_WS_CHILD) | _WS_POPUP)
    _reset_layered(hwnd, _u32.GetWindowLongW(hwnd, _GWL_EXSTYLE))


# 圆角只能自己切：DWM 的圆角属性对任务栏子窗口直接返回 E_HANDLE（实测 0x80070006）。
# 代价是边缘没有抗锯齿，换来的是能真正留在任务栏里面。
_gdi32 = ctypes.windll.gdi32
_gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
_gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
_gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
_gdi32.CreateRectRgn.restype = wintypes.HRGN
_u32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
_u32.SetWindowRgn.restype = ctypes.c_int


def round_corners(hwnd, w, h, radius):
    """把窗口裁成圆角；radius<=0 就恢复矩形。CreateRoundRectRgn 收的是椭圆宽高，故乘 2。"""
    if radius and radius > 0:
        rgn = _gdi32.CreateRoundRectRgn(0, 0, int(w) + 1, int(h) + 1,
                                        int(radius) * 2, int(radius) * 2)
    else:
        rgn = _gdi32.CreateRectRgn(0, 0, int(w) + 1, int(h) + 1)
    _u32.SetWindowRgn(hwnd, rgn, True)   # 成功后区域归系统管，不要自己删


# 实测（三组像素对照）：alpha 恰好 1.0 时 Tk 会去掉分层样式，于是这个子窗口被任务栏的
# DirectComposition(XAML) 层整个盖住——窗口 visible=1、位置也对，但一个像素都不显示。
# alpha<1 时因为带 WS_EX_LAYERED 反而正常。所以这里无条件补上分层样式，
# 这样 100% 不透明也能正常显示。
_WS_EX_LAYERED = 0x00080000
_LWA_ALPHA = 0x00000002
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_SWP_FRAMECHANGED = 0x0020
_u32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD,
                                            ctypes.c_ubyte, wintypes.DWORD]
_u32.SetLayeredWindowAttributes.restype = wintypes.BOOL


def ensure_layered(hwnd, alpha):
    """确保窗口带 WS_EX_LAYERED，并按 alpha 设不透明度。

    没有分层样式的 GDI 子窗口会被任务栏的 DirectComposition(XAML) 层盖住，
    所以这一步不是优化，是显示的必要条件。
    """
    ex = _u32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    if not ex & _WS_EX_LAYERED:
        _u32.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex | _WS_EX_LAYERED)
        # 运行中改扩展样式，要 SWP_FRAMECHANGED 才会立刻生效
        _u32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                          _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOZORDER | _SWP_FRAMECHANGED)
    level = max(0, min(255, int(round(float(alpha) * 255))))
    _u32.SetLayeredWindowAttributes(hwnd, 0, level, _LWA_ALPHA)


# ---- 任务栏歌词条 ---------------------------------------------------------

class Overlay:
    def __init__(self, state, lyrics, on_post):
        self.state = state
        self.lyrics = lyrics
        self.on_post = on_post
        self.key = None
        self.cfg = _load_json(CONFIG_FILE, {})
        for name, value in DEFAULTS.items():
            self.cfg.setdefault(name, value)

        self.root = tk.Tk()
        self.root.title("taskbar lyric")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)

        self.label = tk.Label(self.root, fg=FG, bg=BG, anchor="center", padx=10)
        self.label.pack(fill="both", expand=True)

        self.root.update()          # 先让窗口真正建出来，句柄才有效
        # 必须挂 frame（Tk 的顶层窗口），不能用 winfo_id —— 后者只是它的内容子窗口。
        # 只挂内容窗口的话，Tk 的 geometry 仍然作用在留在桌面上的 frame 上，位置会脱节
        frame = self.root.frame()
        self.hwnd = int(frame, 16) if isinstance(frame, str) else int(frame)
        self.tray = 0
        self.docked = False
        self._ticks = 0
        self._hovered = False
        self._bounds = (0, 0, 0, 0)   # (任务栏宽, 任务栏高, 自身宽, 自身高)
        self._cfg_mtime = self._config_mtime()
        self.apply()

        self.label.bind("<Button-1>", self._grab)
        self.label.bind("<B1-Motion>", self._drag)
        self.label.bind("<ButtonRelease-1>", lambda e: self._save_position())
        self.label.bind("<Button-3>", self._popup)
        self.label.bind("<Enter>", lambda e: self._hover(True))
        self.label.bind("<Leave>", lambda e: self._hover(False))

        self.lock_var = tk.BooleanVar(master=self.root, value=bool(self.cfg.get("lock")))
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="设置", command=lambda: POST.put("settings"))
        menu.add_command(label="隐藏歌词", command=self.root.withdraw)
        menu.add_checkbutton(label="锁定位置", variable=self.lock_var,
                             command=lambda: self.set_lock(self.lock_var.get()))
        menu.add_separator()
        menu.add_command(label="重置位置", command=self._reset)
        menu.add_command(label="退出", command=lambda: POST.put("quit"))
        self.menu = menu

    def apply(self, x=None, y=None, save=True):
        """按当前配置重排字号/透明度/宽度，位置默认保持不变。"""
        cfg = self.cfg
        self.label.config(font=(FONT_FAMILY, cfg["font_size"], "bold"))
        # alpha 恰好 1.0 时 Tk 会摘掉 WS_EX_LAYERED，窗口随即被任务栏的合成层整个盖住
        # （visible=1、位置也对，但一个像素都不显示）。所以让 Tk 始终停在 0.99 以下，
        # 保住分层样式，真正的不透明度由 ensure_layered 设。
        self.root.attributes("-alpha", min(float(cfg["alpha"]), 0.99))
        ensure_layered(self.hwnd, cfg["alpha"])
        color = BG_HOVER if self._hovered else BG
        self.label.config(bg=color)
        self.root.configure(bg=color)
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w = int(screen_w * cfg["width"])
        # 高度对齐 Win11 搜索框（实测 30px）；字调大时让位给字号，别把字切掉
        h = max(int(cfg["height"]), cfg["font_size"] + 8)

        if cfg.get("dock") and not self.docked:
            self._attach()
        elif not cfg.get("dock") and self.docked:
            self._detach()

        if self.docked:
            tray_w, tray_h = taskbar_size(self.tray)
            h = min(h, tray_h)
            self._bounds = (tray_w, tray_h, w, h)
            # 垂直方向固定居中：和 Win11 搜索框在同一条水平线上，只能左右拖
            dy = (tray_h - h) // 2
            dx, dy = self._clamp(cfg.get("dock_x", (tray_w - w) // 2), dy)
            cfg.update(dock_x=dx)
            cfg.pop("dock_y", None)   # 垂直不再可调，清掉旧值免得以后误读
            # 切圆角的 SetWindowRgn 会把窗口打回 (0,0)，所以必须先切圆角再摆位置
            round_corners(self.hwnd, w, h, cfg["radius"])
            # 位置也交给 Tk：SetParent 之后坐标就是相对任务栏的，这样 Tk 的内部状态
            # 才不会和实际位置脱节（否则它下一次刷新会把窗口弹回 0,0）
            self.root.geometry("%dx%d+%d+%d" % (w, h, dx, dy))
            set_child_pos(self.hwnd, dx, dy, w, h)
        else:
            if x is None:
                x = cfg.get("x", (screen_w - w) // 2)
            if y is None:
                y = cfg.get("y", screen_h - h - 4)
            cfg.update(x=x, y=y)
            # 同上：SetWindowRgn 必须早于 geometry，否则窗口被打回 (0,0)
            round_corners(self.hwnd, w, h, 0)
            self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))
        if save:
            _save_json(CONFIG_FILE, cfg)

    @staticmethod
    def _config_mtime():
        try:
            return CONFIG_FILE.stat().st_mtime
        except OSError:
            return 0.0

    def _reload_if_changed(self):
        """设置程序改过 config 就重读并应用——改完立刻生效，不用重启。"""
        stamp = self._config_mtime()
        if not stamp or stamp == self._cfg_mtime:
            return
        # 读不出来就保持现状，下一 tick 再试。绝不能当成「空配置」——
        # 那会把 dock_x 一起清掉，歌词条直接弹回任务栏正中间。
        fresh = _load_json(CONFIG_FILE, None)
        if not isinstance(fresh, dict):
            return
        self._cfg_mtime = stamp
        for name, value in DEFAULTS.items():
            fresh.setdefault(name, value)
        self.cfg.update(fresh)
        # 设置程序「重置位置」是把键从文件里删掉，update 删不掉，得手动清
        for name in ("x", "y", "dock_x", "dock_y"):
            if name not in fresh:
                self.cfg.pop(name, None)
        self.apply(save=False)

    def _save_position(self):
        _save_json(CONFIG_FILE, self.cfg)
        self._cfg_mtime = self._config_mtime()

    def _attach(self):
        """挂到任务栏上；找不到任务栏就退回悬浮。"""
        tray = find_taskbar()
        if not tray:
            self.cfg["dock"] = False
            return
        self.tray = tray
        self.docked = True
        dock_into(self.hwnd, tray)
        ensure_layered(self.hwnd, self.cfg["alpha"])   # 分层位重加过，不透明度要重设

    def _detach(self):
        self.docked = False
        self.tray = 0
        undock_from(self.hwnd)
        self.root.attributes("-topmost", True)
        ensure_layered(self.hwnd, self.cfg["alpha"])

    def _reset(self):
        for key in ("x", "y", "dock_x", "dock_y"):
            self.cfg.pop(key, None)
        self.apply()

    def _clamp(self, x, y):
        """把位置夹在任务栏范围内——拖不出任务栏。"""
        tray_w, tray_h, w, h = self._bounds
        x = max(0, min(int(x), max(0, tray_w - w)))
        y = max(0, min(int(y), max(0, tray_h - h)))
        return x, y

    def _hover(self, on):
        self._hovered = on
        color = BG_HOVER if on else BG
        self.label.config(bg=color)
        self.root.configure(bg=color)

    def set_lock(self, value):
        """锁定位置。设置窗口和右键菜单都走这一个入口，顺便把两边勾选状态对齐。"""
        self.cfg["lock"] = bool(value)
        self.lock_var.set(bool(value))
        _save_json(CONFIG_FILE, self.cfg)
        self._cfg_mtime = self._config_mtime()

    def _grab(self, event):
        self._gx, self._gy = event.x_root, event.y_root
        if self.docked:
            self._sx = self.cfg.get("dock_x", 0)
            self._sy = (self._bounds[1] - self._bounds[3]) // 2   # 垂直固定居中
        else:
            self._sx, self._sy = self.root.winfo_x(), self.root.winfo_y()

    def _drag(self, event):
        if self.cfg.get("lock"):
            return   # 已锁定，拖动无效
        nx = self._sx + (event.x_root - self._gx)
        ny = self._sy + (event.y_root - self._gy)
        if self.docked:
            # 只跟水平拖动，垂直方向始终贴着搜索框那条线
            nx, ny = self._clamp(nx, (self._bounds[1] - self._bounds[3]) // 2)
            self.cfg["dock_x"] = nx
        else:
            self.cfg["x"], self.cfg["y"] = nx, ny
        self.root.geometry("+%d+%d" % (nx, ny))
        if self.docked:
            set_child_pos(self.hwnd, nx, ny, self._bounds[2], self._bounds[3])

    def _popup(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def tick(self):
        self._reload_if_changed()
        self._ticks += 1
        # ponytail: explorer 重启会连带销毁作为子窗口的歌词栏，这里只处理「任务栏
        # 句柄换了但窗口还活着」的情况；要完全自愈得让 Overlay 可以整体重建
        if self.docked and self._ticks % 20 == 0 and not _u32.IsWindow(self.tray):
            self.docked = False
            self._attach()
            self.apply()

        while True:
            try:
                action = POST.get_nowait()
            except queue.Empty:
                break
            self.on_post(action)

        state = self.state
        key = "%s|%s" % (state["title"], state["artist"])
        if state["title"] and key != self.key:
            self.key = key
            self.lyrics.ensure(key, state["title"], state["artist"])

        text = state["error"]
        if state["title"]:
            times, lines = self.lyrics.current(key)
            now = state["pos"]
            if state["playing"] and state["stamp"]:
                now += time.monotonic() - state["stamp"]  # 补上 0.4s 轮询间隔，歌词不跳
            index = line_at(times, now) if times else -1
            text = lines[index] if index >= 0 else ""
            if not text:
                text = "\u266a " + state["title"]
                if state["artist"]:
                    text += " - " + state["artist"]
        self.label.config(text=text)
        self.root.after(TICK_MS, self.tick)


# ---- 拉起设置程序 ---------------------------------------------------------

def _app_dir():
    """本程序所在目录。源码运行和打包成 exe 都指向同一个地方。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _root_dir():
    """往上找同时含 Lyric 和 Lyric setting 的目录 —— exe 在 dist/ 下或直接放同级都能命中。"""
    here = _app_dir()
    for candidate in (here, here.parent, here.parent.parent):
        if (candidate / "Lyric").is_dir() and (candidate / "Lyric setting").is_dir():
            return candidate
    return here.parent


def launch_settings():
    """打开同级的 Lyric setting 程序（优先 exe，退回源码）。"""
    folder = _root_dir() / "Lyric setting"
    for exe in (folder / "LyricSetting.exe", folder / "dist" / "LyricSetting.exe"):
        if exe.exists():
            try:
                subprocess.Popen([str(exe)])
                return True
            except OSError:
                return False
    try:
        subprocess.Popen([sys.executable, str(folder / "setting.py")])
        return True
    except OSError:
        return False


# ---- 托盘 -----------------------------------------------------------------

def _tray_image():
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((10, 36, 32, 58), fill=(235, 235, 242, 255))          # 符头
    draw.rectangle((28, 6, 34, 48), fill=(235, 235, 242, 255))         # 符干
    draw.polygon([(34, 6), (58, 12), (58, 22), (34, 16)],
                 fill=(235, 235, 242, 255))                            # 符尾
    return image


def start_tray():
    menu = pystray.Menu(
        pystray.MenuItem("设置", lambda: POST.put("settings"), default=True),
        pystray.MenuItem("显示 / 隐藏歌词", lambda: POST.put("toggle")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", lambda: POST.put("quit")),
    )
    icon = pystray.Icon("taskbar-lyric", _tray_image(), "taskbar lyric", menu)
    threading.Thread(target=icon.run, daemon=True).start()
    return icon


# ---- 应用 -----------------------------------------------------------------

class App:
    def __init__(self):
        self.state = {"title": "", "artist": "", "playing": False,
                      "pos": 0.0, "stamp": 0.0, "error": ""}
        self.overlay = Overlay(self.state, Lyrics(_load_json(CACHE_FILE, {})), self.handle)
        self.icon = None

    def handle(self, action):
        if action == "quit":
            if self.icon:
                self.icon.stop()
            self.overlay.root.destroy()
        elif action == "toggle":
            root = self.overlay.root
            if root.state() == "withdrawn":
                root.deiconify()
                if self.overlay.docked:
                    self.overlay.apply()  # 子窗口重新显示后要再摆一次位置
                else:
                    root.attributes("-topmost", True)
            else:
                root.withdraw()
        elif action == "settings":
            launch_settings()
    def run(self):
        threading.Thread(target=lambda: asyncio.run(_pump(self.state)), daemon=True).start()
        self.icon = start_tray()
        self.overlay.tick()
        self.overlay.root.mainloop()


def main():
    App().run()


def _selftest():
    times, lines = parse_lrc(
        "[00:01.50]hello\n[00:03.00][00:05.00]again\n[offset:-500]\n"
        "[00:10.00]作词 : someone\nnot a stamp\n"
    )
    assert times == [1.0, 2.5, 4.5], times
    assert lines == ["hello", "again", "again"], lines
    assert line_at(times, 0) == -1
    assert line_at(times, 1.0) == 0
    assert line_at(times, 4.9) == 2
    assert line_at(times, 999) == 2
    assert parse_lrc("no stamps here") == ([], [])

    sample = {}

    async def once():
        sample.update(await _sample(await _SessionManager.request_async()))

    asyncio.run(once())
    print("SMTC sample:", json.dumps(sample, ensure_ascii=True))
    print("selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
