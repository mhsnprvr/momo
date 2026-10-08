#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6F0C3E1A-6B7D-4C55-9C2E-5D3A7B1E9F42}
AppName=MoMo
AppVersion={#AppVersion}
AppPublisher=MoMo
DefaultDirName={localappdata}\Programs\MoMo
DefaultGroupName=MoMo
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=MoMo-Setup
SetupIconFile=MoMo.ico
UninstallDisplayIcon={app}\MoMo.exe
Compression=lzma2/fast
SolidCompression=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\MoMo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\MoMo"; Filename: "{app}\MoMo.exe"
Name: "{autodesktop}\MoMo"; Filename: "{app}\MoMo.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\MoMo.Video"; ValueType: string; ValueData: "Video"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\MoMo.Video\DefaultIcon"; ValueType: string; ValueData: "{app}\MoMo.exe,0"
Root: HKCU; Subkey: "Software\Classes\MoMo.Video\shell\open\command"; ValueType: string; ValueData: """{app}\MoMo.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\.mkv\OpenWithProgids"; ValueType: string; ValueName: "MoMo.Video"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.mp4\OpenWithProgids"; ValueType: string; ValueName: "MoMo.Video"; ValueData: ""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\MoMo.exe"; Description: "Open MoMo"; Flags: nowait postinstall skipifsilent
