param([Parameter(Mandatory=$true)][Guid]$AdapterGuid,[ValidateSet('192.168.1.50','192.168.1.51')][string]$HostAddress='192.168.1.50',[Parameter(Mandatory=$true)][string]$ResultFile)
$ErrorActionPreference='Stop'
$added=$false
$started=$false
if([IO.Path]::GetFileName($ResultFile) -notmatch '^setup-[0-9a-f]{32}\.json$'){throw 'Unexpected result filename'}
$resultParent=Get-Item -LiteralPath ([IO.Path]::GetDirectoryName($ResultFile))
if($resultParent.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Result directory must not be a link'}
try {
    $adapter=@(Get-NetAdapter -Physical|Where-Object {$_.InterfaceGuid -eq $AdapterGuid -and $_.ifType -eq 6})
    if($adapter.Count -ne 1 -or $adapter[0].Status -ne 'Up'){throw 'The selected Ethernet cable is not connected'}
    $index=$adapter[0].ifIndex
    if(Get-NetRoute -InterfaceIndex $index -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue){throw 'This adapter is used for another network; it was not changed'}
    $before=@(Get-NetIPAddress -InterfaceIndex $index -AddressFamily IPv4 -ErrorAction SilentlyContinue)
    if($before|Where-Object {$_.IPAddress -notlike '169.254.*'}){throw 'The adapter configuration changed; refresh before setting up'}
    $dhcp=(Get-NetIPInterface -InterfaceIndex $index -AddressFamily IPv4).Dhcp
    # Default store writes a persistent static address. No gateway or DNS edit.
    $started=$true
    New-NetIPAddress -InterfaceIndex $index -IPAddress $HostAddress -PrefixLength 24 | Out-Null
    $added=$true
    $deadline=(Get-Date).AddSeconds(8)
    do {
        Start-Sleep -Milliseconds 400
        $ip=Get-NetIPAddress -InterfaceIndex $index -IPAddress $HostAddress
    } while($ip.AddressState -eq 'Tentative' -and (Get-Date) -lt $deadline)
    if($ip.AddressState -ne 'Preferred'){throw ('Address not usable: '+$ip.AddressState)}
    $result=@{ok=$true;adapter=$AdapterGuid.ToString();address=($HostAddress+'/24');persistent=$true;gateway_changed=$false;motors_started=$false}
} catch {
    $message=$_.Exception.Message
    if($added){
        Remove-NetIPAddress -InterfaceIndex $index -IPAddress $HostAddress -Confirm:$false -ErrorAction SilentlyContinue
    }
    if($started -and $dhcp -eq 'Enabled'){Set-NetIPInterface -InterfaceIndex $index -AddressFamily IPv4 -Dhcp Enabled -ErrorAction SilentlyContinue}
    $result=@{ok=$false;error=$message;motors_started=$false}
}
$stream=[IO.File]::Open($ResultFile,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try{$bytes=[Text.Encoding]::UTF8.GetBytes(($result|ConvertTo-Json));$stream.Write($bytes,0,$bytes.Length)}finally{$stream.Dispose()}
