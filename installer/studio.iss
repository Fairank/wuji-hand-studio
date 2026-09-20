; studio.iss - Inno Setup 6.3+. Save as UTF-8 with BOM (contains Chinese text).
; ISCC /D: PayloadDir AppExe AppName AppGuid OutputDir OutputName IconFile LicenseFile WebViewBootstrap [AppVersion MutexName ZhIsl]
#if !defined(PayloadDir) || !defined(AppExe) || !defined(AppName) || !defined(AppGuid)
  #error Missing /D define: PayloadDir, AppExe, AppName and AppGuid are required
#endif
#if !defined(OutputDir) || !defined(OutputName) || !defined(IconFile) || !defined(LicenseFile) || !defined(WebViewBootstrap)
  #error Missing /D define: OutputDir, OutputName, IconFile, LicenseFile and WebViewBootstrap are required
#endif
#ifndef AppVersion
  #define AppVersion "0.1.4"
#endif
#ifndef ZhIsl
  #define ZhIsl "compiler:Languages\ChineseSimplified.isl"
#endif
#if LowerCase(AppExe) != "handworkbench.exe"
  #error AppExe must be HandWorkbench.exe
#endif
#define EditionMutex "Local\HandWorkbench"
#ifndef MutexName
  #define MutexName EditionMutex
#endif
#define PayloadRoot AddBackslash(PayloadDir)
#define WebViewFile ExtractFileName(WebViewBootstrap)
#define GuidBare StringChange(StringChange(AppGuid, "{", ""), "}", "")
#if Len(GuidBare) != 36
  #error AppGuid must look like XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX (braces optional)
#endif
#if !FileExists(PayloadRoot + AppExe) || !DirExists(PayloadRoot + "_internal")
  #error PayloadDir must directly contain AppExe and the _internal folder
#endif
#if !FileExists(WebViewBootstrap) || !FileExists(IconFile) || !FileExists(LicenseFile)
  #error WebViewBootstrap, IconFile or LicenseFile does not exist
#endif

[Setup]
AppId={{{#GuidBare}}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
WizardStyle=modern
SetupIconFile={#IconFile}
LicenseFile={#LicenseFile}
UninstallDisplayIcon={app}\{#AppExe}
AppMutex={#MutexName}
CloseApplications=no
RestartApplications=no
Compression=lzma2
SolidCompression=yes
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "zh"; MessagesFile: "{#ZhIsl}"

[CustomMessages]
WebView2Error=Microsoft Edge WebView2 Runtime is required, but it was not detected and its setup did not complete (code %1). Check your network connection, install the WebView2 Runtime from Microsoft, then run this installer again.%n%n本程序需要 Microsoft Edge WebView2 运行时，但系统中未检测到，且其安装未能完成（代码 %1）。请检查网络连接，从微软官方安装 WebView2 运行时后，再重新运行本安装程序。

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Bootstrap first (own solid block) so ExtractTemporaryFile stays fast; it is never copied to the app folder.
Source: "{#WebViewBootstrap}"; Flags: dontcopy
Source: "{#PayloadRoot}*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs solidbreak
Source: "{#ProjectRoot}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ProjectRoot}\scripts\*"; DestDir: "{app}\scripts"; Excludes: "__pycache__\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent unchecked
; No [UninstallDelete]/[InstallDelete]/[Registry] on purpose: user config and recordings outside the app folder are never touched.


[Code]
const
  WV2Name = 'Microsoft Edge WebView2 Runtime';
  WV2Clients = 'SOFTWARE\Microsoft\EdgeUpdate\Clients';

function PvNonZero(const PV: String): Boolean;
var
  S: String;
begin
  S := PV;
  StringChangeEx(S, '0', '', True);
  StringChangeEx(S, '.', '', True);
  Result := Trim(S) <> '';
end;

function ScanClients(Root: Integer; const Key: String; Depth: Integer): Boolean;
var
  Names: TArrayOfString;
  I: Integer;
  N, PV: String;
begin
  Result := False;
  if RegQueryStringValue(Root, Key, 'name', N) then
    if (N = WV2Name) and RegQueryStringValue(Root, Key, 'pv', PV) then
      Result := PvNonZero(PV);
  if Result or (Depth >= 2) then Exit;
  if RegGetSubkeyNames(Root, Key, Names) then
    for I := 0 to GetArrayLength(Names) - 1 do
      if ScanClients(Root, Key + '\' + Names[I], Depth + 1) then
      begin
        Result := True;
        Exit;
      end;
end;

function WebView2Installed: Boolean;
var
  PV: String;
begin
  { Microsoft-documented Evergreen runtime key; do not depend on a localized name. }
  Result := RegQueryStringValue(HKLM32, WV2Clients + '\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', PV) and PvNonZero(PV);
  if not Result then
    Result := RegQueryStringValue(HKCU, WV2Clients + '\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', PV) and PvNonZero(PV);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
  Ok: Boolean;
begin
  Result := '';
  NeedsRestart := False;
  if WebView2Installed then Exit;
  Code := -1;
  try
    ExtractTemporaryFile('{#WebViewFile}');
    Ok := Exec(ExpandConstant('{tmp}') + '\{#WebViewFile}', '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, Code);
  except
    Ok := False;
    Log('WebView2 bootstrap extract/launch failed: ' + GetExceptionMessage);
  end;
  Log('WebView2 bootstrap exit code: ' + IntToStr(Code));
  if Ok then Ok := (Code = 0);
  if Ok then Ok := WebView2Installed;
  if not Ok then Result := FmtMessage(CustomMessage('WebView2Error'), [IntToStr(Code)]);
end;
