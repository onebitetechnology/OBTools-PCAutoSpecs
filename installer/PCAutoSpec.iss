#define MyAppName "PC AutoSpec"
#define MyAppExeName "PCAutoSpec.exe"

[Setup]
AppId={{4A5CF2A4-47A4-4C14-A7D3-29D1F2B5C061}
AppName={#MyAppName}
AppVersion=2.2.45-beta.46
AppPublisher=One Bite Technology
DefaultDirName={autopf}\One Bite Technology\PC AutoSpec
DefaultGroupName=PC AutoSpec
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=PCAutoSpec-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\PCAutoSpec\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\settings.example.json"; DestDir: "{app}"; DestName: "settings.example.json"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\PC AutoSpec"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\PC AutoSpec"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch PC AutoSpec"; Flags: nowait postinstall skipifsilent
