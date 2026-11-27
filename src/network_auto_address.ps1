# Loaded by the elevated network helper. Never enables DHCP, DNS, or devices.

function Initialize-WujiIpHelper {
    if('WujiAutomaticIpHelper' -as [type]){return}
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Net;
using System.Runtime.InteropServices;

public static class WujiAutomaticIpHelper {
    // SOCKADDR_INET is sized for its largest member, SOCKADDR_IN6.
    [StructLayout(LayoutKind.Sequential, Size = 28)]
    private struct Address {
        public ushort Family;
        public ushort Port;
        public uint IPv4;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct Neighbor {
        public Address Address;
        public uint Index;
        public ulong Luid;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 32)]
        public byte[] Mac;
        public uint MacLength;
        public uint State;
        public byte Flags;
        public uint LastReachable;
    }

    [DllImport("iphlpapi.dll", ExactSpelling = true)]
    private static extern uint AddIPAddress(uint address, uint mask, uint index, out uint context, out uint instance);
    [DllImport("iphlpapi.dll", ExactSpelling = true)]
    private static extern uint DeleteIPAddress(uint context);
    [DllImport("iphlpapi.dll", ExactSpelling = true)]
    private static extern uint ResolveIpNetEntry2(ref Neighbor row, ref Address source);

    private static Address IPv4(string value) {
        return new Address { Family = 2, IPv4 = BitConverter.ToUInt32(IPAddress.Parse(value).GetAddressBytes(), 0) };
    }
    private static void Check(uint code) {
        if (code != 0) throw new Win32Exception((int)code);
    }
    public static uint AddTemporary(uint index, string source) {
        uint context;
        uint instance;
        Check(AddIPAddress(IPv4(source).IPv4, IPv4("255.255.255.0").IPv4, index, out context, out instance));
        return context;
    }
    public static void RemoveTemporary(uint context) {
        Check(DeleteIPAddress(context));
    }
    public static string Resolve(uint index, string source, string target) {
        var targetAddress = IPv4(target);
        var sourceAddress = IPv4(source);
        var row = new Neighbor { Address = targetAddress, Index = index, Mac = new byte[32] };
        uint code = ResolveIpNetEntry2(ref row, ref sourceAddress);
        if (code == 67) return null; // ERROR_BAD_NET_NAME: unreachable on this link.
        Check(code);
        if (row.Index != index || row.Address.Family != 2 || row.Address.IPv4 != targetAddress.IPv4 ||
            row.MacLength != 6 || (row.Flags & 2) != 0 || (row.Mac[0] & 1) != 0 ||
            (row.Mac[0] | row.Mac[1] | row.Mac[2] | row.Mac[3] | row.Mac[4] | row.Mac[5]) == 0)
            throw new InvalidOperationException("Windows returned a different path or no usable hardware address");
        return BitConverter.ToString(row.Mac, 0, 6);
    }
}
'@ -ErrorAction Stop
}

function Add-WujiTemporaryAddress([uint32]$InterfaceIndex,[string]$Address) {
    Initialize-WujiIpHelper
    [WujiAutomaticIpHelper]::AddTemporary($InterfaceIndex,$Address)
}

function Remove-WujiTemporaryAddress([uint32]$Context) {
    Initialize-WujiIpHelper
    [WujiAutomaticIpHelper]::RemoveTemporary($Context)
}

function Resolve-WujiNeighbor([uint32]$InterfaceIndex,[string]$Source,[string]$Target) {
    Initialize-WujiIpHelper
    [WujiAutomaticIpHelper]::Resolve($InterfaceIndex,$Source,$Target)
}

function Remove-WujiAutomaticAddress {
    param([Parameter(Mandatory=$true)][uint32]$Context)
    # The native context identifies the exact address created by this attempt.
    # Keep cleanup available after cancellation or interface replacement.
    Remove-WujiTemporaryAddress -Context $Context
}

function Get-WujiAutomaticAddresses([string]$Family,[string]$Store,[uint32]$InterfaceIndex) {
    $query=@{AddressFamily=$Family;PolicyStore=$Store;ErrorAction='Stop'}
    if($InterfaceIndex){$query.InterfaceIndex=$InterfaceIndex}
    try { Get-NetIPAddress @query }
    catch { if($_.CategoryInfo.Category -ne 'ObjectNotFound'){throw} }
}

function Confirm-WujiAutomaticAdapter([uint32]$InterfaceIndex,[string]$HostAddress,[switch]$TemporaryPresent) {
    Confirm-Scan
    Confirm-DedicatedAdapter
    $adapter=@(Get-NetAdapter -Physical -ErrorAction Stop | Where-Object {$_.ifIndex -eq $InterfaceIndex})
    if($adapter.Count -ne 1 -or [Guid]$adapter[0].InterfaceGuid -ne $AdapterGuid -or
       $adapter[0].InterfaceType -ne 6 -or $adapter[0].Status -ne 'Up'){
        throw 'The new Ethernet adapter changed before automatic setup'
    }
    foreach($store in 'ActiveStore','PersistentStore'){
        $v4=@(Get-WujiAutomaticAddresses 'IPv4' $store $InterfaceIndex)
        if($v4 | Where-Object {$_.IPAddress -notlike '169.254.*' -and
            (!$TemporaryPresent -or $store -ne 'ActiveStore' -or $_.IPAddress -ne $HostAddress -or $_.PrefixLength -ne 24)}){
            throw 'The replacement Ethernet adapter already has an IPv4 configuration'
        }
        $v6=@(Get-WujiAutomaticAddresses 'IPv6' $store $InterfaceIndex)
        if($v6 | Where-Object {$_.IPAddress -notmatch '^fe[89ab][0-9a-f]:'}){
            throw 'The replacement Ethernet adapter already has an IPv6 configuration'
        }
        $default6=@(try { Get-NetRoute -InterfaceIndex $InterfaceIndex -AddressFamily IPv6 -DestinationPrefix '::/0' -PolicyStore $store -ErrorAction Stop }
            catch { if($_.CategoryInfo.Category -ne 'ObjectNotFound'){throw} })
        if($default6.Count){throw 'The replacement Ethernet adapter has an IPv6 gateway'}
    }
}

function Test-WujiOnLink([string]$Source,[int]$Length,[string]$Target) {
    if($Length -lt 1 -or $Length -gt 32){return $false}
    $first=[Net.IPAddress]::Parse($Source).GetAddressBytes()
    $second=[Net.IPAddress]::Parse($Target).GetAddressBytes()
    for($position=0;$position -lt 4 -and $Length -gt 0;$position++){
        $shift=[Math]::Max(0,8-$Length)
        if(($first[$position] -shr $shift) -ne ($second[$position] -shr $shift)){return $false}
        $Length-=8
    }
    return $true
}

function Get-WujiAutomaticProbeLinks([string]$Target) {
    $addresses=@(Get-WujiAutomaticAddresses 'IPv4' 'ActiveStore')
    if($addresses | Where-Object IPAddress -eq $Target){throw 'The device address is assigned to this computer'}
    $adapters=@(Get-NetAdapter -ErrorAction Stop | Where-Object {$_.Status -eq 'Up' -and $_.InterfaceType -eq 6})
    foreach($adapter in $adapters){
        $source=@($addresses | Where-Object {$_.InterfaceIndex -eq $adapter.ifIndex -and
            $_.AddressState -eq 'Preferred' -and (Test-WujiOnLink $_.IPAddress $_.PrefixLength $Target)} |
            Sort-Object IPAddress | Select-Object -First 1)
        if(!$source.Count){continue}
        if(@($addresses | Where-Object IPAddress -eq $source[0].IPAddress).Count -ne 1){
            throw 'Multiple adapters share a possible source address'
        }
        [pscustomobject]@{index=[uint32]$adapter.ifIndex;guid=([Guid]$adapter.InterfaceGuid).ToString()
                         source=[string]$source[0].IPAddress}
    }
}

function Get-WujiLinkKeys($Links) {
    @($Links | ForEach-Object {"$($_.index)/$($_.guid)/$($_.source)"} | Sort-Object)
}

function Get-WujiFreshNeighbors {
    param([Parameter(Mandatory=$true)]
          [ValidateSet('192.168.1.100','192.168.1.101','192.168.1.110','192.168.1.111')][string]$Target)
    Confirm-Scan
    Confirm-DedicatedAdapter
    $links=@(Get-WujiAutomaticProbeLinks $Target)
    $original=(Get-WujiLinkKeys $links) -join ','
    $responses=@()
    foreach($link in $links){
        Confirm-Scan
        Confirm-DedicatedAdapter
        if(((Get-WujiLinkKeys (Get-WujiAutomaticProbeLinks $Target)) -join ',') -cne $original){
            throw 'Ethernet adapters changed before probing the device'
        }
        $mac=Resolve-WujiNeighbor -InterfaceIndex $link.index -Source $link.source -Target $Target
        if($mac){$responses+=@{index=$link.index;mac=$mac}}
    }
    Confirm-Scan
    if(((Get-WujiLinkKeys (Get-WujiAutomaticProbeLinks $Target)) -join ',') -cne $original){
        throw 'Ethernet adapters changed during the device probe'
    }
    return $responses
}

function Initialize-WujiAutomaticAddress {
    [CmdletBinding(DefaultParameterSetName='Exact')]
    param([Parameter(Mandatory=$true)][ValidateRange(1,2147483647)][uint32]$InterfaceIndex,
          [Parameter(Mandatory=$true)][ValidatePattern('^192\.168\.1\.[5-9][0-9]$')][string]$HostAddress,
          [Parameter(Mandatory=$true,ParameterSetName='Exact')][ValidateSet('192.168.1.100','192.168.1.101','192.168.1.110','192.168.1.111')][string]$DeviceAddress,
          [Parameter(Mandatory=$true,ParameterSetName='Candidate')][ValidateCount(1,4)][ValidateSet('192.168.1.100','192.168.1.101','192.168.1.110','192.168.1.111')][string[]]$CandidateAddresses)
    $targets=@(if($DeviceAddress){$DeviceAddress}else{$CandidateAddresses | Select-Object -Unique})
    Confirm-WujiAutomaticAdapter $InterfaceIndex $HostAddress
    $allAddresses=@(Get-WujiAutomaticAddresses 'IPv4' 'ActiveStore')+@(Get-WujiAutomaticAddresses 'IPv4' 'PersistentStore')
    if($allAddresses | Where-Object IPAddress -eq $HostAddress){throw 'The temporary IPv4 address is already assigned'}
    $targets=@($targets | Where-Object {$_ -notin $allAddresses.IPAddress})
    if(!$targets.Count){throw 'The device address is assigned to this computer'}
    Confirm-Scan
    Confirm-DedicatedAdapter
    $context=Add-WujiTemporaryAddress -InterfaceIndex $InterfaceIndex -Address $HostAddress
    try {
        $elapsed=[Diagnostics.Stopwatch]::StartNew()
        do {
            Confirm-Scan
            $local=@(Get-WujiAutomaticAddresses 'IPv4' 'ActiveStore' $InterfaceIndex |
                Where-Object IPAddress -eq $HostAddress)
            if($local.Count -gt 1 -or ($local.Count -eq 1 -and $local[0].AddressState -notin 'Tentative','Preferred')){
                throw 'The temporary IPv4 address is duplicated or unusable'
            }
            if($local.Count -eq 1 -and $local[0].AddressState -eq 'Preferred'){break}
            Start-Sleep -Milliseconds 150
        } while($elapsed.Elapsed.TotalSeconds -lt 6)
        if($local.Count -ne 1 -or $local[0].AddressState -ne 'Preferred'){
            throw 'The temporary IPv4 address did not become usable'
        }
        Confirm-WujiAutomaticAdapter $InterfaceIndex $HostAddress -TemporaryPresent
        $choices=@()
        foreach($target in $targets){
            $responses=@(Get-WujiFreshNeighbors -Target $target)
            $onReplacement=@($responses | Where-Object {$_.index -eq $InterfaceIndex})
            if($onReplacement.Count){
                if($responses.Count -ne 1){throw 'Multiple Ethernet adapters answered for the same device address'}
                $choices+=@{context=$context;mac=$onReplacement[0].mac;device=$target}
            }
        }
        if($choices.Count -ne 1){throw 'A unique device was not found on the new Ethernet adapter'}
        Confirm-WujiAutomaticAdapter $InterfaceIndex $HostAddress -TemporaryPresent
        return $choices[0]
    } catch {
        $reason=$_.Exception.Message
        try { Remove-WujiAutomaticAddress $context }
        catch { throw "$reason; temporary IPv4 cleanup unconfirmed: $($_.Exception.Message)" }
        throw
    }
}
