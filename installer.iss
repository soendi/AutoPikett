#ifndef MyAppVersion
#define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{5C1B7E42-9D3A-4F86-B7E0-2A4C8D6F1B93}
AppName=AutoPikett
AppVersion={#MyAppVersion}
AppPublisher=AutoPikett
AppPublisherURL=https://github.com/soendi/AutoPikett
DefaultDirName={localappdata}\Programs\AutoPikett
DefaultGroupName=AutoPikett
; Installation pro Benutzer: Updates laufen ohne Admin-Rechte und ohne UAC.
PrivilegesRequired=lowest

OutputDir=installer
OutputBaseFilename=AutoPikett-Setup-{#MyAppVersion}

SetupIconFile=assets\AutoPikett.ico
UninstallDisplayIcon={app}\AutoPikett.exe

Compression=lzma2
SolidCompression=yes
WizardStyle=modern

; Laufende App darf ersetzt werden (auch bei stillen Updates).
CloseApplications=yes
CloseApplicationsFilter=AutoPikett.exe
RestartApplications=no

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Files]
Source: "dist\AutoPikett\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AutoPikett"; Filename: "{app}\AutoPikett.exe"; IconFilename: "{app}\AutoPikett.exe"
Name: "{autodesktop}\AutoPikett"; Filename: "{app}\AutoPikett.exe"; IconFilename: "{app}\AutoPikett.exe"

[Registry]
; Autostart, damit der Zeitplan nach dem Login läuft.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "AutoPikett"; ValueData: """{app}\AutoPikett.exe"""; Flags: uninsdeletevalue

[Run]
; Nach der Installation starten - auch bei stillen Updates.
Filename: "{app}\AutoPikett.exe"; Flags: nowait postinstall

[Code]
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  // Laufende Instanz beenden, damit die Dateien ersetzt werden können.
  Exec('cmd.exe', '/C taskkill /F /IM AutoPikett.exe 2>nul', '', SW_HIDE, ewNoWait, ResultCode);
  Result := True;
end;
