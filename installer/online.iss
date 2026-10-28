; Small update entry for older workbenches whose downloader has a short budget.
; Uses Inno Setup's original download page and the unchanged offline installer.
#if !defined(AppVersion) || !defined(PayloadName) || !defined(PayloadUrl) || !defined(PayloadSha256) || !defined(PayloadBytes)
  #error Missing immutable offline installer identity
#endif
#if !defined(OutputDir) || !defined(OutputName) || !defined(IconFile) || !defined(ZhIsl)
  #error Missing output assets
#endif

[Setup]
AppId={{44DF38B2-39A0-491F-B154-E587231C83CE}
AppName=Hand Workbench
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
MinVersion=10.0
CreateAppDir=no
Uninstallable=no
UsePreviousAppDir=no
UsePreviousLanguage=no
DisableWelcomePage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
DisableProgramGroupPage=yes
DisableStartupPrompt=yes
CloseApplications=no
RestartApplications=no
WizardStyle=modern
SetupIconFile={#IconFile}
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "zh"; MessagesFile: "{#ZhIsl}"

[CustomMessages]
en.DownloadTitle=Downloading Hand Workbench
zh.DownloadTitle=正在下载灵巧手工作台
en.DownloadDescription=Download and verify the complete installation package. Your settings and calibration profiles are kept.
zh.DownloadDescription=正在下载并校验完整安装包，保留你的设置与标定档案。
en.DownloadFailed=Download did not complete. No application files were changed. Check your connection and retry.
zh.DownloadFailed=下载未完成，未修改软件文件。请检查连接后重试。
en.DownloadCancelled=Download cancelled. No application files were changed.
zh.DownloadCancelled=已取消下载，未修改软件文件。

[Code]
var
  DownloadPage: TDownloadWizardPage;
  Verified: Boolean;
  ChildExitCode: Integer;

function VerySilent: Boolean;
var
  Index: Integer;
begin
  Result := False;
  for Index := 1 to ParamCount do
    if CompareText(ParamStr(Index), '/VERYSILENT') = 0 then Result := True;
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  Result := True;
#ifdef AuditDirectory
  // Compile-time QA only: never included by the production builder.
  SaveStringToFile('{#AuditDirectory}\progress.log', IntToStr(Progress) + '/' + IntToStr(ProgressMax) + #13#10, True);
  #ifdef AuditCancelAfter
  Result := Progress < {#AuditCancelAfter};
  #endif
#endif
end;

procedure InitializeWizard;
begin
  ChildExitCode := 1;
  DownloadPage := CreateDownloadPage(CustomMessage('DownloadTitle'), CustomMessage('DownloadDescription'), @OnDownloadProgress);
  DownloadPage.ShowBaseNameInsteadOfUrl := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  PayloadPath: String;
  Bytes: Int64;
begin
  Result := '';
  Verified := False;
  PayloadPath := ExpandConstant('{tmp}\{#PayloadName}');
  DownloadPage.Clear;
  DownloadPage.Add('{#PayloadUrl}', '{#PayloadName}', '{#PayloadSha256}');
  // PrepareToInstall also runs during /SILENT. Show the real download page
  // before any application file can be installed, including in older updaters.
  if not VerySilent then WizardForm.Show;
  DownloadPage.Show;
  try
    try
      DownloadPage.Download;
      if not FileSize64(PayloadPath, Bytes) or (Bytes <> {#PayloadBytes}) then
        RaiseException('Installation package length does not match the published version');
      if CompareText(GetSHA256OfFile(PayloadPath), '{#PayloadSha256}') <> 0 then
        RaiseException('Installation package checksum does not match the published version');
      Verified := True;
      Log('Verified exact offline installation package: {#PayloadSha256}');
    except
      Log('Download failed: ' + GetExceptionMessage);
      ChildExitCode := 8;
      if DownloadPage.AbortedByUser then begin
        ChildExitCode := 2;
        Result := CustomMessage('DownloadCancelled');
      end else
        Result := CustomMessage('DownloadFailed');
    end;
  finally
    DownloadPage.Hide;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Arguments, Destination, PayloadPath: String;
begin
  if CurStep <> ssPostInstall then Exit;
  PayloadPath := ExpandConstant('{tmp}\{#PayloadName}');
  if not Verified or (CompareText(GetSHA256OfFile(PayloadPath), '{#PayloadSha256}') <> 0) then
    RaiseException('The verified installation package changed');
#ifdef AuditDownloadOnly
  // A separate QA executable may retain the public bytes without installing.
  if not FileCopy(PayloadPath, '{#AuditDirectory}\verified-payload.exe', True) then
    RaiseException('Cannot retain the isolated download evidence');
  ChildExitCode := 0;
#else
  Arguments := '/SP- /NORESTART /CURRENTUSER /NOCLOSEAPPLICATIONS /NORESTARTAPPLICATIONS /LOG /LANG=' + ActiveLanguage;
  if VerySilent then
    Arguments := Arguments + ' /VERYSILENT /SUPPRESSMSGBOXES'
  else if WizardSilent then
    Arguments := Arguments + ' /SILENT /SUPPRESSMSGBOXES';
  Destination := ExpandConstant('{param:DIR|}');
  if Destination <> '' then begin
    if (Pos('"', Destination) > 0) or (Pos(#13, Destination) > 0) or (Pos(#10, Destination) > 0) then
      RaiseException('Invalid installation directory');
    Arguments := Arguments + ' /DIR="' + Destination + '"';
  end;
  WizardForm.Hide;
  if not Exec(PayloadPath, Arguments, '', SW_SHOW, ewWaitUntilTerminated, ChildExitCode) then begin
    Log('Could not start the verified offline installer');
    ChildExitCode := 1;
  end;
#endif
  Log('Offline installer result: ' + IntToStr(ChildExitCode));
end;

function GetCustomSetupExitCode: Integer;
begin
  // Only the original offline installer's success permits the updater to
  // verify the new edition and restart. Cancellation/errors never become 0.
  Result := ChildExitCode;
end;
