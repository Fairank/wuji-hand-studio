param([Parameter(Mandatory=$true)][string]$PlanFile,[switch]$ValidateOnly)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$plan=$null
$resultValidated=$false
$versionVerified=$false
$launchStarted=$false
$applicationExited=$false
function PlainPath([string]$path) {
    $item=Get-Item -LiteralPath $path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Update paths must not be redirects' }
    return $item
}
function Result([string]$phase,[string]$errorText='') {
    if ($null -eq $plan -or -not $resultValidated) { return }
    $record=@{token=$plan.token;phase=$phase;version=$plan.version;sha256=$plan.sha256;error=$errorText;version_verified=$versionVerified;launch_started=$launchStarted;at=[DateTime]::UtcNow.ToString('o')}
    [IO.File]::WriteAllText($plan.result_file,($record|ConvertTo-Json -Compress),(New-Object Text.UTF8Encoding($false)))
}
try {
    $file=PlainPath $PlanFile
    if ($file.Length -gt 65536) { throw 'Update plan exceeds its limit' }
    $plan=Get-Content -LiteralPath $PlanFile -Raw -Encoding UTF8|ConvertFrom-Json
    if ($plan.schema -ne 1 -or $plan.app_id -cne '44DF38B2-39A0-491F-B154-E587231C83CE' -or $plan.repository -cne 'Fairank/wuji-hand-studio' -or $plan.token -cnotmatch '^[a-f0-9]{32}$' -or $plan.sha256 -cnotmatch '^[a-f0-9]{64}$' -or $plan.version -cnotmatch '^\d{1,4}\.\d{1,4}\.\d{1,4}$') { throw 'Invalid application update identity' }
    $root=[IO.Path]::GetFullPath($plan.data_dir)
    $updates=Join-Path $root 'updates'
    $expectedPlan=Join-Path $updates ('apply-'+$plan.token+'.json')
    $expectedResult=Join-Path $updates ('apply-'+$plan.token+'.result.json')
    if ([IO.Path]::GetFullPath($PlanFile) -ne $expectedPlan -or $plan.result_file -ne $expectedResult) { $plan=$null;throw 'Unexpected update plan location' }
    [void](PlainPath $root);[void](PlainPath $updates)
    if (Test-Path -LiteralPath $expectedResult) { [void](PlainPath $expectedResult) }
    $resultValidated=$true
    $installer=Join-Path $updates ('HandWorkbench-'+$plan.version+'-windows-x64-setup.exe')
    if ($plan.installer -ne $installer) { throw 'Unexpected staged installer path' }
    $payload=PlainPath $installer
    if ($payload.Length -le 2 -or $payload.Length -gt 1073741824 -or (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -cne $plan.sha256) { throw 'Installer hash or size mismatch' }
    $exe=[IO.Path]::GetFullPath($plan.executable)
    if ([IO.Path]::GetFileName($exe) -cne 'HandWorkbench.exe') { throw 'Unexpected application executable' }
    [void](PlainPath $exe)
    $origin=[Uri]$plan.origin
    if ($origin.Scheme -cne 'http' -or $origin.Host -cne '127.0.0.1' -or $origin.Port -lt 1024 -or $origin.Port -gt 65535 -or $origin.AbsolutePath -ne '/' -or $origin.Query -or $origin.Fragment -or $origin.UserInfo) { throw 'Expected the local workbench origin' }
    $base=$origin.GetLeftPart([UriPartial]::Authority)
    $info=Invoke-RestMethod -Uri ($base+'/api/desktop') -TimeoutSec 10
    if (-not $info.native -or $info.edition -cne 'workbench' -or $info.version -cne $plan.old_version -or [int]$info.pid -ne [int]$plan.expected_app_pid -or [IO.Path]::GetFullPath($info.data_dir) -ne $root) { throw 'The running workbench identity changed' }
    $appProcess=Get-Process -Id $info.pid
    if ($appProcess.Path -ne $exe) { throw 'The workbench executable identity changed' }
    $started=$appProcess.StartTime.ToUniversalTime().Ticks
    $state=Invoke-RestMethod -Uri ($base+'/api/state') -TimeoutSec 10
    $headers=@{'X-Console-Token'=$state.csrf;'Origin'=$base}
    # Each server request has one 10 s budget, including discovery yielding,
    # hash/preflight and close cleanup. The client leaves response headroom.
    # A timeout is a failed attempt; never retry close/install automatically.
    $request=@{operation='prepare_update';version=$plan.version;sha256=$plan.sha256}|ConvertTo-Json -Compress
    $ready=Invoke-RestMethod -Uri ($base+'/api/desktop') -Method Post -Headers $headers -ContentType 'application/json' -Body $request -TimeoutSec 15
    if (-not $ready.ok -or $ready.sha256 -cne $plan.sha256) { throw 'Application update preflight failed' }
    if ($ValidateOnly) { Result 'validated';exit 0 }
    $request=@{operation='close_idle'}|ConvertTo-Json -Compress
    try {
        $closing=Invoke-RestMethod -Uri ($base+'/api/desktop') -Method Post -Headers $headers -ContentType 'application/json' -Body $request -TimeoutSec 15
        if (-not $closing.ok) { throw 'The workbench could not close while idle' }
    } catch {
        if (Get-Process -Id $info.pid -ErrorAction SilentlyContinue) { throw }
    }
    $deadline=[DateTime]::UtcNow.AddSeconds(25)
    while ($true) {
        $live=Get-Process -Id $info.pid -ErrorAction SilentlyContinue
        if (-not $live -or $live.StartTime.ToUniversalTime().Ticks -ne $started) { break }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Application did not exit; installer was not started' }
        Start-Sleep -Milliseconds 200
    }
    $applicationExited=$true
    [void](PlainPath $installer)
    if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -cne $plan.sha256) { throw 'Installer changed after preflight' }
    # Inno's existing progress window stays visible after the workbench closes.
    # /SILENT skips setup questions but does not hide installation progress.
    $arguments=@('/SILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER','/NOCLOSEAPPLICATIONS','/NORESTARTAPPLICATIONS',('/DIR="'+[IO.Path]::GetDirectoryName($exe)+'"'))
    Result 'installing'
    $installed=Start-Process -FilePath $installer -ArgumentList $arguments -WindowStyle Normal -PassThru -Wait
    if ($installed.ExitCode -ne 0) { throw ('Installer exited with code '+$installed.ExitCode) }
    $editionPath=Join-Path ([IO.Path]::GetDirectoryName($exe)) '_internal\edition.json'
    $editionFile=PlainPath $editionPath
    if ($editionFile.Length -ge 65536) { throw 'Installed application identity exceeds its limit' }
    $edition=Get-Content -LiteralPath $editionPath -Raw -Encoding UTF8|ConvertFrom-Json
    if ($edition.name -cne 'workbench' -or $edition.product_id -cne 'hand-workbench' -or $edition.version -cne $plan.version) { throw 'Installed application version does not match the update' }
    [void](PlainPath $exe)
    $versionVerified=$true
    Result 'restarting'
    $restarted=Start-Process -FilePath $exe -WindowStyle Normal -PassThru
    if ($null -eq $restarted -or $restarted.Id -le 0) { throw 'Updated application did not start' }
    $launchStarted=$true
    Result 'installed'
    exit 0
} catch {
    $failureText=$_.Exception.Message
    try { Result 'error' $failureText } catch { }
    if ($applicationExited) {
        # The original window is gone. Use the system dialog so this failure
        # cannot disappear into a hidden helper's result file.
        try {
            $notice=New-Object -ComObject WScript.Shell
            [void]$notice.Popup(('Hand Workbench update did not complete.'+[Environment]::NewLine+[Environment]::NewLine+$failureText+[Environment]::NewLine+[Environment]::NewLine+'Open Hand Workbench and retry the update.'),0,'Hand Workbench',16)
        } catch { }
    }
    exit 1
}
