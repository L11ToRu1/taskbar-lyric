"""Lyric setting —— 任务栏歌词的设置程序（Material 3 Expressive 风格）。

只负责一件事：编辑 %LOCALAPPDATA%\\taskbar-lyric\\config.json。
Lyric 程序会监听这个文件，所以改完立刻生效，不用重启。
"""

import ctypes
import json
import os
import sys
import tkinter as tk
import winreg
from ctypes import wintypes
from pathlib import Path

try:
    import customtkinter as ctk
except ImportError:
    sys.exit("缺少依赖，请先运行:  pip install -r requirements.txt")

APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "taskbar-lyric"
CONFIG_FILE = APP_DIR / "config.json"
FONT_FAMILY = "Microsoft YaHei UI"


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


LYRIC_DIR = _root_dir() / "Lyric"

DEFAULTS = {
    "font_size": 16,
    "alpha": 0.82,
    "width": 0.6,
    "dock": True,
    "height": 30,
    "radius": 10,
    "lock": False,
}

# ---- Material 3 深色配色 ----
M3_SURFACE = "#141218"
M3_CONTAINER = "#211F26"
M3_CONTAINER_HIGH = "#2B2930"
M3_PRIMARY = "#D0BCFF"
M3_ON_PRIMARY = "#381E72"
M3_ON_PRIMARY_CONTAINER = "#EADDFF"
M3_SECONDARY_CONTAINER = "#4A4458"
M3_ON_SECONDARY_CONTAINER = "#E8DEF8"
M3_ON_SURFACE = "#E6E0E9"
M3_ON_SURFACE_VARIANT = "#CAC4D0"
M3_OUTLINE = "#49454F"
M3_RADIUS_CARD = 28
M3_RADIUS_PILL = 22


# ---- 配置读写 -------------------------------------------------------------

def load_config():
    """读配置。文件不存在 -> 默认值；存在但读不出来 -> None。

    返回 None 时调用方要退回自己上次读到的内容，绝不能拿默认值写回去——
    那会把用户的设置连同 dock_x 一起冲掉。
    """
    try:
        raw = CONFIG_FILE.read_text("utf-8")
    except FileNotFoundError:
        cfg = {}
    except OSError:
        return None          # 正被 Lyric 原子替换，等下次再读
    else:
        try:
            cfg = json.loads(raw)
        except ValueError:
            return None      # 半截 JSON，同上
    for key, value in DEFAULTS.items():
        cfg.setdefault(key, value)
    return cfg


def save_config(cfg):
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        # ponytail: 非原子写，同 lyric.py。Lyric 读到半截文件会跳过这次重载（它那边的 guard），
        # 不会再把 dock_x 当成「用户没设过」。
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False), "utf-8")
    except OSError:
        pass


# ---- 开机自启（注册的是 Lyric 程序，不是本程序） --------------------------

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "TaskbarLyric"


def lyric_command():
    """自启要拉起歌词程序。优先用打包好的 exe，没有就退回 pythonw + 脚本。"""
    for exe in (LYRIC_DIR / "Lyric.exe", LYRIC_DIR / "dist" / "Lyric.exe"):
        if exe.exists():
            return '"%s"' % exe
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    launcher = pythonw if pythonw.exists() else Path(sys.executable)
    return '"%s" "%s"' % (launcher, LYRIC_DIR / "lyric.py")


def is_autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _RUN_NAME)
        return True
    except OSError:
        return False


def set_autostart(enabled):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ, lyric_command())
        else:
            try:
                winreg.DeleteValue(key, _RUN_NAME)
            except OSError:
                pass


# ---- DWM 圆角（顶层窗口才有抗锯齿） ---------------------------------------

_dwm = ctypes.windll.dwmapi
_dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                       ctypes.c_void_p, wintypes.DWORD]
_dwm.DwmSetWindowAttribute.restype = ctypes.c_long
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUND = 2


def set_dwm_round(hwnd):
    value = ctypes.c_int(_DWMWCP_ROUND)
    _dwm.DwmSetWindowAttribute(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE,
                               ctypes.byref(value), ctypes.sizeof(value))


# ---- 设置窗口 -------------------------------------------------------------

class SettingsWindow:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config() or dict(DEFAULTS)

        ctk.set_appearance_mode("dark")
        win = self.win = ctk.CTkToplevel(root)
        win.title("Lyric setting")
        win.overrideredirect(True)          # 去掉系统边框，标题栏自己画
        win.attributes("-topmost", True)
        win.configure(fg_color=M3_SURFACE)

        self._header(win)
        self._appearance_card(win)
        self._behaviour_card(win)
        self._buttons(win)

        win.bind("<Escape>", lambda e: root.destroy())
        win.update_idletasks()
        self._place(win)
        win.update()
        frame = win.frame()
        set_dwm_round(int(frame, 16) if isinstance(frame, str) else int(frame))
        win.focus_force()

    # ---- 自绘标题栏（可拖动） ----
    def _header(self, win):
        bar = ctk.CTkFrame(win, fg_color="transparent", height=44)
        bar.pack(fill="x", padx=16, pady=(14, 2))
        bar.pack_propagate(False)

        title = tk.Label(bar, text="Lyric setting", bg=M3_SURFACE, fg=M3_ON_SURFACE,
                         font=(FONT_FAMILY, 15, "bold"))
        title.pack(side="left", padx=(10, 0))

        ctk.CTkButton(bar, text="✕", width=34, height=34, corner_radius=17,
                      fg_color=M3_CONTAINER_HIGH, hover_color=M3_OUTLINE,
                      text_color=M3_ON_SURFACE, font=(FONT_FAMILY, 13),
                      command=self.root.destroy).pack(side="right", padx=(0, 4))

        for widget in (bar, title):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)

    def _drag_start(self, event):
        self._dx = event.x_root - self.win.winfo_x()
        self._dy = event.y_root - self.win.winfo_y()

    def _drag_move(self, event):
        self.win.geometry("+%d+%d" % (event.x_root - self._dx, event.y_root - self._dy))

    # ---- 卡片 ----
    @staticmethod
    def _card(parent, heading):
        card = ctk.CTkFrame(parent, fg_color=M3_CONTAINER, corner_radius=M3_RADIUS_CARD)
        card.pack(fill="x", padx=16, pady=(4, 12))
        ctk.CTkLabel(card, text=heading, text_color=M3_PRIMARY,
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=20, pady=(16, 4))
        return card

    def _appearance_card(self, win):
        card = self._card(win, "外观")
        rows = (
            ("字号", "font_size", 10, 40, 1, "%d"),
            ("不透明度", "alpha", 0.3, 1.0, 0.02, "%.2f"),
            ("宽度占比", "width", 0.2, 1.0, 0.05, "%.2f"),
            ("高度", "height", 20, 48, 1, "%d"),   # 搜索框实测 30
            ("圆角", "radius", 0, 16, 1, "%d"),    # 搜索框实测 10
        )
        for text, key, low, high, step, fmt in rows:
            head = ctk.CTkFrame(card, fg_color="transparent")
            head.pack(fill="x", padx=20, pady=(6, 0))
            ctk.CTkLabel(head, text=text, text_color=M3_ON_SURFACE_VARIANT,
                         font=ctk.CTkFont(size=13)).pack(side="left")
            value_label = ctk.CTkLabel(head, text=fmt % self.cfg[key],
                                       text_color=M3_ON_SURFACE,
                                       font=ctk.CTkFont(size=13, weight="bold"))
            value_label.pack(side="right")

            steps = max(1, int(round((high - low) / step)))
            slider = ctk.CTkSlider(card, from_=low, to=high, number_of_steps=steps,
                                   fg_color=M3_OUTLINE, progress_color=M3_PRIMARY,
                                   button_color=M3_ON_PRIMARY_CONTAINER,
                                   button_hover_color=M3_ON_PRIMARY_CONTAINER)
            slider.set(self.cfg[key])
            slider.pack(fill="x", padx=20, pady=(0, 12))
            # 先把值设好，回调最后挂，免得初始化时把配置冲掉
            slider.configure(command=self._slider_handler(key, fmt, value_label))

    def _slider_handler(self, key, fmt, value_label):
        def handler(value):
            shown = int(round(value)) if fmt == "%d" else round(float(value), 2)
            value_label.configure(text=fmt % shown)
            self._save({key: shown})
        return handler

    def _behaviour_card(self, win):
        card = self._card(win, "行为")
        self.dock = self._switch(card, "镶嵌到任务栏",
                                 bool(self.cfg.get("dock")), self._toggle_dock)
        self.lock = self._switch(card, "锁定位置",
                                 bool(self.cfg.get("lock")), self._toggle_lock)
        self.autostart = self._switch(card, "开机自启",
                                      is_autostart(), self._toggle_autostart)
        ctk.CTkFrame(card, fg_color="transparent", height=8).pack()

    def _switch(self, card, text, value, command):
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=6)
        ctk.CTkLabel(row, text=text, text_color=M3_ON_SURFACE_VARIANT,
                     font=ctk.CTkFont(size=13)).pack(side="left")
        switch = ctk.CTkSwitch(row, text="", width=46,
                               fg_color=M3_OUTLINE, progress_color=M3_PRIMARY,
                               button_color=M3_ON_PRIMARY_CONTAINER,
                               button_hover_color=M3_ON_PRIMARY_CONTAINER)
        switch.pack(side="right")
        (switch.select if value else switch.deselect)()
        switch.configure(command=command)   # 同上：状态设好再挂回调
        return switch

    def _buttons(self, win):
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(4, 18))
        ctk.CTkButton(row, text="重置位置", height=44, corner_radius=M3_RADIUS_PILL,
                      fg_color=M3_SECONDARY_CONTAINER, hover_color=M3_OUTLINE,
                      text_color=M3_ON_SECONDARY_CONTAINER,
                      font=ctk.CTkFont(size=14, weight="bold"),
                      command=self._reset).pack(side="left", expand=True, fill="x",
                                                padx=(0, 6))
        ctk.CTkButton(row, text="关闭", height=44, corner_radius=M3_RADIUS_PILL,
                      fg_color=M3_PRIMARY, hover_color=M3_ON_PRIMARY_CONTAINER,
                      text_color=M3_ON_PRIMARY,
                      font=ctk.CTkFont(size=14, weight="bold"),
                      command=self.root.destroy).pack(side="left", expand=True,
                                                      fill="x", padx=(6, 0))

    def _place(self, win):
        width = max(400, win.winfo_reqwidth())
        height = win.winfo_reqheight()
        screen_w, screen_h = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry("%dx%d+%d+%d" % (width, height,
                                      max(0, (screen_w - width) // 2),
                                      max(40, (screen_h - height) // 2 - 60)))

    # ---- 写配置 ----------------------------------------------------------
    def _save(self, changes):
        """合并式写入：先重读文件再改，避免把 Lyric 刚更新的位置覆盖掉。"""
        cfg = dict(load_config() or self.cfg)   # 读失败就退回上次读到的，别用默认值覆盖
        cfg.update(changes)
        save_config(cfg)
        self.cfg = cfg

    def _toggle_dock(self):
        self._save({"dock": bool(self.dock.get())})

    def _toggle_lock(self):
        self._save({"lock": bool(self.lock.get())})

    def _toggle_autostart(self):
        set_autostart(bool(self.autostart.get()))

    def _reset(self):
        cfg = dict(load_config() or self.cfg)
        for key in ("x", "y", "dock_x"):
            cfg.pop(key, None)
        save_config(cfg)
        self.cfg = cfg


def main():
    root = ctk.CTk()
    root.withdraw()
    SettingsWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
