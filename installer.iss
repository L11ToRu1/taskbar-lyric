; Taskbar Lyric 安装程序脚本
; 编译：  iscc installer.iss    （或双击 build_installer.bat）
; 需要 Inno Setup 6：winget install JRSoftware.InnoSetup

#define AppName "Taskbar Lyric"
; 版本号只在根目录的 VERSION 里写一次，这个文件由 make_version.py 生成
#include "version.iss"

[Setup]
AppId={{8E3F2C1A-5B4D-4E7F-9A21-3C6D5E8F1A02}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}.0
AppPublisher=Mizuak1
AppPublisherURL=https://github.com/Mizuak1/taskbar-lyric
DefaultDirName={autopf}\TBLyric
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer
OutputBaseFilename=TBLyricSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
; 两个 exe 必须留在各自的文件夹里。Lyric 靠「同级同时存在 Lyric 和 Lyric setting」
; 这两个目录」来定位设置程序，铺平放会让托盘里的「设置」失效。
Source: "Lyric\dist\Lyric.exe"; DestDir: "{app}\Lyric"; Flags: ignoreversion
Source: "Lyric setting\dist\LyricSetting.exe"; DestDir: "{app}\Lyric setting"; Flags: ignoreversion

[Icons]
Name: "{group}\Taskbar Lyric"; Filename: "{app}\Lyric\Lyric.exe"
Name: "{group}\Lyric setting"; Filename: "{app}\Lyric setting\LyricSetting.exe"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Taskbar Lyric"; Filename: "{app}\Lyric\Lyric.exe"; Tasks: desktopicon

[Registry]
; 开机自启项是程序自己写进 Run 的，卸载时顺手清掉
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "TaskbarLyric"; Flags: dontcreatekey uninsdeletevalue

[Run]
Filename: "{app}\Lyric\Lyric.exe"; Description: "启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c taskkill /f /im Lyric.exe"; Flags: runhidden; RunOnceId: "KillLyric"
Filename: "{cmd}"; Parameters: "/c taskkill /f /im TaskBarLyric.exe"; Flags: runhidden; RunOnceId: "KillTBLyric"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var R: Integer;
begin
  { 覆盖安装前先关掉正在跑的实例，否则 exe 被占用会写不进去 }
  Exec('taskkill.exe', '/f /im Lyric.exe', '', SW_HIDE, ewWaitUntilTerminated, R);
  Exec('taskkill.exe', '/f /im TaskBarLyric.exe', '', SW_HIDE, ewWaitUntilTerminated, R);
  Result := '';
end;
