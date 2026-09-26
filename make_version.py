"""从一个地方生成两处需要的版本信息。

改版本只改根目录的 VERSION 文件，然后照常跑 build.bat / build_installer.bat ——
installer.iss 和两个 exe 的版本资源都从这里来，不用再手改第二处。

生成的东西都是派生文件，已加进 .gitignore：
    Lyric/version_info.txt          给 PyInstaller 的 --version-file
    Lyric setting/version_info.txt
    version.iss                     给 installer.iss 的 #include
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = (ROOT / "VERSION").read_text("utf-8").strip()
if not re.fullmatch(r"\d+\.\d+\.\d+", RAW):
    raise SystemExit("VERSION 里应该是一行 x.y.z，现在是 %r" % RAW)

NUMS = tuple(int(x) for x in RAW.split("."))
QUAD = "%d, %d, %d, %d" % (NUMS + (0,) * (4 - len(NUMS)))

TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(%(quad)s),
    prodvers=(%(quad)s),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'080404B0',
        [StringStruct(u'CompanyName', u'Mizuak1'),
         StringStruct(u'FileDescription', u'%(desc)s'),
         StringStruct(u'FileVersion', u'%(ver)s'),
         StringStruct(u'InternalName', u'%(internal)s'),
         StringStruct(u'OriginalFilename', u'%(filename)s'),
         StringStruct(u'ProductName', u'Taskbar Lyric'),
         StringStruct(u'ProductVersion', u'%(ver)s')])
    ]),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
"""

TARGETS = (
    ("Lyric", "Lyric.exe", "Taskbar Lyric 歌词显示"),
    ("Lyric setting", "LyricSetting.exe", "Taskbar Lyric 歌词设置"),
)

for folder, exe, desc in TARGETS:
    text = TEMPLATE % {
        "quad": QUAD,
        "ver": RAW,
        "desc": desc,
        "internal": exe.rsplit(".", 1)[0],
        "filename": exe,
    }
    (ROOT / folder / "version_info.txt").write_text(text, "utf-8")
    print("wrote %s/version_info.txt" % folder)

(ROOT / "version.iss").write_text('#define AppVersion "%s"\n' % RAW, "utf-8")
print("wrote version.iss  (AppVersion = %s)" % RAW)
