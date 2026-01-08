; PulzWaveArtNetMidiBridge Installer Script
; Inno Setup Script
; https://jrsoftware.org/isinfo.php

#define MyAppName "PulzWaveArtNetMidiBridge"
#define MyAppPublisher "PulzWave"
#define MyAppURL "https://github.com/PulzWave/PulzWaveArtNetMidiBridge"
#define MyAppExeName "PulzWaveArtNetMidiBridge.exe"

; Version is passed from command line: /DMyAppVersion=1.0.0
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{8F4E3B2A-1C5D-4E6F-9A8B-7C2D1E0F3A4B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=PulzWaveArtNetMidiBridge-{#MyAppVersion}-Setup
SetupIconFile=..\src\image\pulzwave_icon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
function IsLoopMIDIInstalled(): Boolean;
var
  LoopMIDIPath: String;
begin
  Result := False;
  
  // Check common installation paths
  LoopMIDIPath := ExpandConstant('{pf}\Tobias Erichsen\loopMIDI\loopMIDI.exe');
  if FileExists(LoopMIDIPath) then
  begin
    Result := True;
    Exit;
  end;
  
  // Check 32-bit path on 64-bit Windows
  LoopMIDIPath := ExpandConstant('{pf32}\Tobias Erichsen\loopMIDI\loopMIDI.exe');
  if FileExists(LoopMIDIPath) then
  begin
    Result := True;
    Exit;
  end;
  
  // Check registry for loopMIDI
  if RegKeyExists(HKEY_LOCAL_MACHINE, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\loopMIDI') then
  begin
    Result := True;
    Exit;
  end;
  
  if RegKeyExists(HKEY_LOCAL_MACHINE, 'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\loopMIDI') then
  begin
    Result := True;
    Exit;
  end;
end;

function InitializeSetup(): Boolean;
var
  ErrorCode: Integer;
begin
  Result := True;
  
  if not IsLoopMIDIInstalled() then
  begin
    if MsgBox('PulzWaveArtNetMidiBridge requires loopMIDI to be installed.' + #13#10 + #13#10 +
              'loopMIDI is a free virtual MIDI port driver that allows the app to send MIDI to other applications.' + #13#10 + #13#10 +
              'Would you like to open the loopMIDI download page?' + #13#10 + #13#10 +
              'Click Yes to open the download page and install loopMIDI first.' + #13#10 +
              'Click No to continue anyway (you can install loopMIDI later).',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      ShellExec('open', 'https://www.tobias-erichsen.de/software/loopmidi.html', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
      MsgBox('Please install loopMIDI, then run this installer again.', mbInformation, MB_OK);
      Result := False;
    end;
  end;
end;
