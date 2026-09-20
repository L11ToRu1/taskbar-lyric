# taskbar lyric

在 Windows 任务栏上显示当前播放歌曲的歌词。

项目分成**两个独立的程序**，各自一个文件夹、各自一个 exe，通过配置文件通信：

| 文件夹 | 程序 | 作用 |
| --- | --- | --- |
| `Lyric\` | `Lyric.exe` | 歌词显示：托盘常驻，把歌词画在任务栏里 |
| `Lyric setting\` | `LyricSetting.exe` | 设置界面：Material 3 Expressive 风格，只负责改配置 |

两者共享 `%LOCALAPPDATA%\taskbar-lyric\config.json`。歌词程序每 100ms 检查该文件的修改时间，
一变就重新读取并应用——所以设置里拖滑块是**实时生效**的，不用重启，两个程序也互不依赖。

## 怎么用

仓库里只放源码，exe 要先自己打包（见下面「自己打包」，各文件夹里跑一次 `build.bat`）。

1. 双击 `Lyric\dist\Lyric.exe` —— 歌词条出现在任务栏里，托盘出现图标
2. 要调外观时，双击 `Lyric setting\dist\LyricSetting.exe`（或从托盘 / 歌词条右键菜单打开）

## 各自的说明

- `Lyric\README.md` —— 歌词显示、操作方式、两个实现约束、已知限制
- `Lyric setting\README.md` —— 各项设置的含义

## 从源码运行

```
cd Lyric            && pip install -r requirements.txt && pythonw lyric.py
cd "Lyric setting"  && pip install -r requirements.txt && python  setting.py
```

## 自己打包

各文件夹里都有 `build.bat`，产物落在各自的 `dist\` 下。

> `Lyric` 打包必须带那几个 `--hidden-import winrt.*`（winrt 是命名空间包，PyInstaller 静态分析不到）；
> `Lyric setting` 必须带 `--collect-all customtkinter`（主题资源）。两个都已经写在各自的 build.bat 里。
