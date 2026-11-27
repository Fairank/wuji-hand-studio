param([Parameter(Mandatory=$true)][Guid]$AdapterGuid,
      [Parameter(Mandatory=$true)][ValidateRange(1,2147483647)][int]$ExpectedInterfaceIndex,
      [ValidatePattern('^192\.168\.1\.[5-9][0-9]$')][string]$HostAddress,
      [ValidatePattern('^192\.168\.1\.(?:[1-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-4])$')][string]$TargetAddress,
      [ValidateSet('192.168.1.100','192.168.1.101','192.168.1.110','192.168.1.111')][string]$DeviceAddress,
      [string]$CandidateAddresses,
      [Guid]$PreviousAdapterGuid,
      [ValidateRange(1,2147483647)][int]$PreviousInterfaceIndex,
      [ValidatePattern('^(?:[0-9a-fA-F]{12}|[0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})$')][string]$NeighborMac,
      [string]$ScanMarker,
      [ValidatePattern('^[0-9a-f]{32}$')][string]$ScanToken,
      [string]$PreviousReceiptPath,
      [string]$PreviousRouteState,
      [string]$RestoreAddressReceipt,
      [Parameter(Mandatory=$true)][string]$ResultFile)
$ErrorActionPreference='Stop'
$added=$false
$started=$false
$routeStarted=$false
$oldRemovalStarted=$false
$temporaryContext=$null
$observed=[bool]$PreviousRouteState
$recorded=($null -ne $PreviousAdapterGuid -and $PreviousAdapterGuid -ne [Guid]::Empty) -or
          $PreviousInterfaceIndex -or $PreviousReceiptPath
$recovery=$observed -or $recorded
$restore=[bool]$RestoreAddressReceipt
$automatic=$recovery -or $restore -or $NeighborMac -or $ScanMarker -or $ScanToken -or $CandidateAddresses
$factoryAddresses=@('192.168.1.100','192.168.1.101','192.168.1.110','192.168.1.111')
$metric=5
$index=$ExpectedInterfaceIndex
$stores=@('ActiveStore','PersistentStore')
if([IO.Path]::GetFileName($ResultFile) -notmatch '^setup-[0-9a-f]{32}\.json$'){throw 'Unexpected result filename'}
$resultParent=Get-Item -LiteralPath ([IO.Path]::GetDirectoryName($ResultFile))
if($resultParent.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Result directory must not be a link'}

function Get-DeviceRoutes([string]$Store) {
    try {
        Get-NetRoute -AddressFamily IPv4 -PolicyStore $Store -ErrorAction Stop |
            Where-Object {$_.DestinationPrefix -eq $prefix}
    } catch {
        if($_.CategoryInfo.Category -ne 'ObjectNotFound'){throw}
    }
}

function Confirm-Adapter([switch]$RequireLink) {
    $adapter=@(Get-NetAdapter -Physical|Where-Object {([Guid]$_.InterfaceGuid) -eq $AdapterGuid -and $_.InterfaceType -eq 6})
    if($adapter.Count -ne 1 -or $adapter[0].ifIndex -ne $index){
        throw 'The selected adapter changed interface index; refresh the network check'
    }
    if($RequireLink -and $adapter[0].Status -ne 'Up'){
        throw 'The selected Ethernet cable is not connected'
    }
}

function Read-SmallJson([string]$Path) {
    $file=Get-Item -LiteralPath $Path
    if($file.PSIsContainer -or $file.Length -gt 262144 -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)){
        throw 'Unexpected network setup evidence file'
    }
    Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Read-RouteReceipt([string]$Path,[Guid]$RouteAdapter,[int]$RouteIndex) {
    $receiptPath=[IO.Path]::GetFullPath($Path)
    if([IO.Path]::GetDirectoryName($receiptPath) -ine $resultParent.FullName -or
       [IO.Path]::GetFileName($receiptPath) -notmatch '^setup-[0-9a-f]{32}\.json$' -or
       $receiptPath -ieq [IO.Path]::GetFullPath($ResultFile)){throw 'Unexpected previous route receipt'}
    $saved=Read-SmallJson $receiptPath
    if($saved.ok -isnot [bool] -or !$saved.ok -or $saved.persistent -isnot [bool] -or !$saved.persistent -or
       $saved.route -cne $prefix -or [string]$saved.adapter -ine $RouteAdapter.ToString() -or
       ($null -ne $saved.interface_index -and $saved.interface_index -ne $RouteIndex) -or
       ($null -ne $saved.route_metric -and $saved.route_metric -ne $metric)){
        throw 'Previous route receipt does not match this request'
    }
    $saved
}

function Confirm-Scan([switch]$AllowCancelled) {
    if([IO.Path]::GetFileName($ScanMarker) -cne 'session.json'){throw 'Unexpected discovery marker'}
    $marker=Read-SmallJson $ScanMarker
    if($marker.kind -cne 'scan' -or $marker.token -cne $ScanToken -or
       $marker.pid -isnot [int] -or $marker.pid -le 0 -or
       $marker.start -isnot [string] -or $marker.start -notmatch '^[0-9]{16,20}$'){
        throw 'Discovery ownership changed before network recovery'
    }
    $owner=Get-Process -Id $marker.pid -ErrorAction Stop
    if($owner.StartTime.ToUniversalTime().ToFileTimeUtc().ToString() -cne $marker.start){
        throw 'Discovery ownership changed before network recovery'
    }
    $cancel=Join-Path ([IO.Path]::GetDirectoryName($ScanMarker)) ('network-cancel-'+$ScanToken)
    if(!$AllowCancelled -and (Test-Path -LiteralPath $cancel)){
        throw 'Discovery was cancelled before network recovery'
    }
}

function Confirm-DedicatedAdapter {
    Confirm-Adapter -RequireLink
    foreach($store in $stores){
        $defaultRoute=@(try { Get-NetRoute -InterfaceIndex $index -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -PolicyStore $store -ErrorAction Stop }
            catch { if($_.CategoryInfo.Category -ne 'ObjectNotFound'){throw} })
        if($defaultRoute.Count){throw 'This adapter is used for another network; it was not changed'}
    }
}

function Confirm-Subnet($Addresses,$SavedAddresses) {
    if(($Addresses+$SavedAddresses) | Where-Object {$_.IPAddress -eq $DeviceAddress}){throw 'The device address is assigned to this computer'}
    $own=@($Addresses | Where-Object {$_.InterfaceIndex -eq $index -and $_.AddressState -eq 'Preferred' -and $_.PrefixLength -eq 24 -and $_.IPAddress -like '192.168.1.*'})
    if($own.Count -eq 0 -or ($automatic -and $HostAddress -and $HostAddress -notin $own.IPAddress)){
        throw 'The selected adapter no longer has a usable device subnet'
    }
}

function Confirm-PreviousAdapter {
    $adapters=@(Get-NetAdapter -IncludeHidden -ErrorAction Stop)
    $old=@($adapters | Where-Object {
        [string]$_.InterfaceGuid -in $PreviousAdapterGuid.ToString(),$PreviousAdapterGuid.ToString('B')
    })
    $byIndex=@($adapters | Where-Object {$_.ifIndex -eq $PreviousInterfaceIndex})
    if($old.Count -gt 1 -or ($old.Count -eq 1 -and
       ($byIndex.Count -ne 1 -or $old[0].ifIndex -ne $PreviousInterfaceIndex -or
        $old[0].InterfaceType -ne 6 -or $old[0].Status -notin 'Disconnected','Down','Not Present','NotPresent','Disabled')) -or
       ($old.Count -eq 0 -and ($byIndex.Count -ne 0 -or (!$observed -and !$absentInstance))) -or
       ($observed -and (($old.Count -eq 0) -ne ($null -eq $routeState.old_guid)))){
        throw 'The previous adapter identity or disconnect could not be confirmed'
    }
    if($observed){
        if($old.Count){
            $physical=@(Get-NetAdapter -Physical -IncludeHidden -ErrorAction Stop | Where-Object {
                $_.ifIndex -eq $PreviousInterfaceIndex -and
                [string]$_.InterfaceGuid -in $PreviousAdapterGuid.ToString(),$PreviousAdapterGuid.ToString('B')
            })
            if($physical.Count -ne 1){throw 'The previous adapter is not uniquely identified as a physical Ethernet adapter'}
        } elseif(@(Get-NetIPInterface -IncludeAllCompartments -ErrorAction Stop | Where-Object {
            $_.InterfaceIndex -eq $PreviousInterfaceIndex
        }).Count){
            throw 'The previous interface index still belongs to an IP interface'
        }
        foreach($store in $stores){
            $defaults=@(try { Get-NetRoute -PolicyStore $store -ErrorAction Stop | Where-Object {
                $_.InterfaceIndex -eq $PreviousInterfaceIndex -and $_.DestinationPrefix -in '0.0.0.0/0','::/0'
            } }
                catch { if($_.CategoryInfo.Category -ne 'ObjectNotFound'){throw} })
            if($defaults.Count){throw 'The previous interface still holds a default route'}
        }
    }
    return ($old.Count -eq 0)
}

function Confirm-Recovery {
    Confirm-Scan
    Confirm-DedicatedAdapter
    $currentAddresses=@(Get-NetIPAddress -AddressFamily IPv4 -PolicyStore ActiveStore -ErrorAction Stop)
    $persistentAddresses=@(try { Get-NetIPAddress -AddressFamily IPv4 -PolicyStore PersistentStore -ErrorAction Stop }
        catch { if($_.CategoryInfo.Category -ne 'ObjectNotFound'){throw} })
    Confirm-Subnet $currentAddresses $persistentAddresses
    $previousMissing=if($recovery){Confirm-PreviousAdapter}else{$false}
    $expectedMac=$NeighborMac -replace '[-:]'
    $reachable=@(try { Get-NetNeighbor -AddressFamily IPv4 -IPAddress $DeviceAddress -PolicyStore ActiveStore -ErrorAction Stop |
        Where-Object {$_.IPAddress -eq $DeviceAddress -and $_.State -eq 'Reachable'} }
        catch { if($_.CategoryInfo.Category -ne 'ObjectNotFound'){throw} })
    if($reachable.Count -ne 1 -or $reachable[0].InterfaceIndex -ne $index -or
       ($reachable[0].LinkLayerAddress -replace '[-:]') -ine $expectedMac){
        throw 'A unique reachable device on the replacement adapter could not be confirmed'
    }
    return $previousMissing
}

function Test-ManagedRoute($Route,[int]$RouteIndex) {
    $Route.InterfaceIndex -eq $RouteIndex -and $Route.NextHop -eq '0.0.0.0' -and
    $Route.RouteMetric -eq $metric -and [string]$Route.Protocol -in 'NetMgmt','3'
}

function Confirm-RestorableRoute([switch]$RequireActive) {
    $saved=@(Get-DeviceRoutes 'PersistentStore')
    $active=@(Get-DeviceRoutes 'ActiveStore')
    if($saved.Count -ne 1 -or !(Test-ManagedRoute $saved[0] $index) -or
       [string]$saved[0].InstanceID -cne $receipt.route_instance -or
       $active.Count -gt 1 -or ($active.Count -eq 1 -and !(Test-ManagedRoute $active[0] $index)) -or
       ($RequireActive -and !$active.Count)){
        throw 'The existing device route changed before address restoration'
    }
    return ($active.Count -eq 0)
}

function Read-MigrationStore([string]$Store,[bool]$PreviousMissing,[bool]$ExpectNew,[switch]$RequireOld) {
    $routes=@(Get-DeviceRoutes $Store)
    $old=@()
    if($observed){
        $instance=if($Store -eq 'ActiveStore'){$routeState.active_instance}else{$routeState.persistent_instance}
        $old=@($routes | Where-Object {
            $_.InterfaceIndex -eq $PreviousInterfaceIndex -and $_.NextHop -eq '0.0.0.0' -and
            $_.RouteMetric -eq $routeState.route_metric -and [string]$_.Protocol -in 'NetMgmt','3' -and
            [string]$_.Publish -in 'No','0' -and $instance -and [string]$_.InstanceID -ceq $instance
        })
        if($RequireOld -and $old.Count -ne [int][bool]$instance){throw 'An observed route changed before replacement'}
    } elseif($recorded) {
        $old=@($routes | Where-Object {Test-ManagedRoute $_ $PreviousInterfaceIndex})
    }
    $new=@($routes | Where-Object {Test-ManagedRoute $_ $index})
    if($old.Count -gt 1 -or $new.Count -ne [int]$ExpectNew -or
       $routes.Count -ne ($old.Count+$new.Count) -or
       ($recorded -and $old.Count -eq 1 -and $Store -eq 'PersistentStore' -and $receipt.route_instance -and
        [string]$old[0].InstanceID -cne [string]$receipt.route_instance) -or
       ($recorded -and $PreviousMissing -and $old.Count -eq 1 -and
        ($Store -ne 'PersistentStore' -or [string]$old[0].InstanceID -cne $absentInstance))){
        throw 'An existing route could not be attributed to this recovery'
    }
    @{old=$old;new=$new}
}

try {
    Confirm-DedicatedAdapter
    $addresses=@(Get-NetIPAddress -AddressFamily IPv4 -PolicyStore ActiveStore -ErrorAction Stop)
    $savedAddresses=@(try { Get-NetIPAddress -AddressFamily IPv4 -PolicyStore PersistentStore -ErrorAction Stop }
        catch { if($_.CategoryInfo.Category -ne 'ObjectNotFound'){throw} })
    if($automatic){
        if(!$ScanMarker -or !$ScanToken -or $TargetAddress -or (!$DeviceAddress -and !$CandidateAddresses) -or
           ($HostAddress -and $NeighborMac) -or (!$HostAddress -and !$NeighborMac)){
            throw 'Incomplete automatic network setup request'
        }
        Confirm-Scan
        if($restore -and (!$DeviceAddress -or !$HostAddress -or $CandidateAddresses -or $recovery)){
            throw 'Incomplete temporary address restoration request'
        }
        if($CandidateAddresses){
            if($DeviceAddress -or $recovery -or !$HostAddress){throw 'Choose one type of automatic network setup'}
            $targets=$CandidateAddresses.Split(',')
            if($targets.Count -gt $factoryAddresses.Count -or
               @($targets|Select-Object -Unique).Count -ne $targets.Count -or
               @($targets|Where-Object {$_ -cnotin $factoryAddresses}).Count){
                throw 'Unexpected automatic device address candidates'
            }
        }
    }
    if($DeviceAddress -or $CandidateAddresses){
        if($TargetAddress -or ($HostAddress -and !$automatic)){throw 'Choose one type of device network setup'}
        if($DeviceAddress){$prefix=$DeviceAddress+'/32'}
        if($restore){
            $receipt=Read-RouteReceipt $RestoreAddressReceipt $AdapterGuid $index
            if($receipt.interface_index -ne $index -or $receipt.route_metric -ne $metric -or
               $receipt.route_instance -isnot [string] -or !$receipt.route_instance -or
               $receipt.route_instance.Length -gt 1024 -or $receipt.temporary_address -isnot [string] -or
               $receipt.temporary_address -notmatch '^192\.168\.1\.[5-9][0-9]/24$'){
                throw 'Previous route receipt does not confirm this temporary address'
            }
        } elseif($observed){
            if($recorded){throw 'Supply either a previous receipt or observed routes'}
            $observedPath=[IO.Path]::GetFullPath($PreviousRouteState)
            if([IO.Path]::GetDirectoryName($observedPath) -ine $resultParent.FullName -or
               [IO.Path]::GetFileName($observedPath) -cnotmatch '^observed-route-[0-9a-f]{32}\.json$'){
                throw 'Unexpected observed route state'
            }
            $routeState=Read-SmallJson $observedPath
            $fields=@('prefix','old_index','old_guid','route_metric','active_instance','persistent_instance')
            $names=@($routeState.PSObject.Properties.Name)
            if($names.Count -ne $fields.Count -or @($names|Where-Object {$_ -cnotin $fields}).Count -or
               $routeState.prefix -cne $prefix -or $routeState.old_index -isnot [int] -or
               $routeState.old_index -lt 1 -or $routeState.old_index -eq $index -or
               $routeState.route_metric -isnot [int] -or $routeState.route_metric -lt 0 -or
               $routeState.route_metric -gt 65535 -or (!$routeState.active_instance -and !$routeState.persistent_instance)){
                throw 'Invalid observed route state'
            }
            foreach($instance in $routeState.active_instance,$routeState.persistent_instance){
                if($null -ne $instance -and ($instance -isnot [string] -or !$instance -or $instance.Length -gt 1024)){
                    throw 'Invalid observed route instance'
                }
            }
            $PreviousInterfaceIndex=$routeState.old_index
            $PreviousAdapterGuid=[Guid]::Empty
            if($null -ne $routeState.old_guid -and
               ($routeState.old_guid -isnot [string] -or
                ![Guid]::TryParseExact($routeState.old_guid,'D',[ref]$PreviousAdapterGuid) -or
                $PreviousAdapterGuid -in [Guid]::Empty,$AdapterGuid)){
                throw 'Invalid observed adapter identity'
            }
        } elseif($recorded){
            if(!$PreviousAdapterGuid -or $PreviousAdapterGuid -eq [Guid]::Empty -or !$PreviousInterfaceIndex -or
               $PreviousInterfaceIndex -eq $index -or $PreviousAdapterGuid -eq $AdapterGuid -or !$PreviousReceiptPath){
                throw 'Incomplete route recovery request'
            }
            $receipt=Read-RouteReceipt $PreviousReceiptPath $PreviousAdapterGuid $PreviousInterfaceIndex
            $absentInstance=if($receipt.interface_index -eq $PreviousInterfaceIndex -and $receipt.route_metric -eq $metric -and
                $receipt.route_instance -is [string] -and $receipt.route_instance.Length -le 1024){$receipt.route_instance}else{''}
        }
        if($automatic){
            $previousMissing=if($recovery){Confirm-PreviousAdapter}else{$false}
            $oldCount=0
            if($restore){
                $null=Confirm-RestorableRoute
            } elseif($CandidateAddresses){
                foreach($target in $targets){
                    $prefix=$target+'/32'
                    foreach($store in $stores){$null=Read-MigrationStore $store $false $false}
                }
            } else {
                foreach($store in $stores){$oldCount+=(Read-MigrationStore $store $previousMissing $false -RequireOld).old.Count}
            }
            if($recovery -and $oldCount -eq 0){throw 'The previous route is no longer present'}
            if($HostAddress){
                . (Join-Path $PSScriptRoot 'network_auto_address.ps1')
                $parameters=@{InterfaceIndex=$index;HostAddress=$HostAddress}
                if($CandidateAddresses){$parameters.CandidateAddresses=$targets}else{$parameters.DeviceAddress=$DeviceAddress}
                $setup=Initialize-WujiAutomaticAddress @parameters
                $temporaryContext=$setup.context
                if($temporaryContext -isnot [uint32] -or
                   $setup.mac -notmatch '^[0-9a-fA-F]{2}(-[0-9a-fA-F]{2}){5}$'){
                    throw 'Unexpected temporary address confirmation'
                }
                if($CandidateAddresses){
                    if($setup.device -cnotin $targets){throw 'Unexpected discovered factory address'}
                    $DeviceAddress=$setup.device
                    $prefix=$DeviceAddress+'/32'
                } elseif($setup.device -cne $DeviceAddress){throw 'Unexpected discovered factory address'}
                $NeighborMac=$setup.mac
            } elseif($observed){
                Confirm-Subnet $addresses $savedAddresses
                . (Join-Path $PSScriptRoot 'network_auto_address.ps1')
                $neighbors=@(Get-WujiFreshNeighbors -Target $DeviceAddress)
                if($neighbors.Count -ne 1 -or $neighbors[0].index -ne $index -or
                   ($neighbors[0].mac -replace '[-:]') -ine ($NeighborMac -replace '[-:]')){
                    throw 'A fresh probe did not confirm a unique device on the replacement adapter'
                }
            }
            $previousMissing=Confirm-Recovery
            if($restore){
                $null=Confirm-RestorableRoute
            } else {
                foreach($store in $stores){$null=Read-MigrationStore $store $previousMissing $false -RequireOld}
            }
            Confirm-Scan
        } else {
            Confirm-Subnet $addresses $savedAddresses
            if(@(Get-DeviceRoutes 'ActiveStore').Count -or @(Get-DeviceRoutes 'PersistentStore').Count){
                throw 'A route for this device already exists; refresh the connection check'
            }
        }
        # The cmdlet's default saves to both stores. Only this device's /32
        # route is added; the adapter address and gateway are not changed.
        Confirm-Adapter -RequireLink
        if($automatic){Confirm-Scan}
        if(!$restore -or (Confirm-RestorableRoute)){
            $routeStarted=$true
            $parameters=@{DestinationPrefix=$prefix;InterfaceIndex=$index;NextHop='0.0.0.0';RouteMetric=$metric;ErrorAction='Stop'}
            if($restore){$parameters.PolicyStore='ActiveStore'}
            New-NetRoute @parameters | Out-Null
        }
        if($automatic){
            $previousMissing=Confirm-Recovery
            if($restore){
                $null=Confirm-RestorableRoute -RequireActive
            } else {
                foreach($store in $stores){$null=Read-MigrationStore $store $previousMissing $true -RequireOld}
            }
        }
        if($recovery){
            foreach($store in $stores){
                $previousMissing=Confirm-Recovery
                $rows=Read-MigrationStore $store $previousMissing $true
                if($rows.old.Count -eq 1){
                    Confirm-Scan
                    $oldRemovalStarted=$true
                    try {
                        Remove-NetRoute -InputObject $rows.old[0] -Confirm:$false -ErrorAction Stop
                    } catch {
                        if((Read-MigrationStore $store $previousMissing $true).old.Count){throw}
                    }
                    if((Read-MigrationStore $store $previousMissing $true).old.Count){throw 'Previous device route removal was not verified'}
                }
            }
        }
        if($automatic){Confirm-Scan}
        $instance=''
        foreach($store in $stores){
            $verified=@(Get-DeviceRoutes $store)
            if($verified.Count -ne 1 -or $verified[0].InterfaceIndex -ne $index -or
               $verified[0].NextHop -ne '0.0.0.0' -or $verified[0].RouteMetric -ne $metric -or
               ($automatic -and !(Test-ManagedRoute $verified[0] $index))){
                throw 'The exact device route was not verified'
            }
            if($store -eq 'PersistentStore'){$instance=[string]$verified[0].InstanceID}
        }
        Confirm-Adapter -RequireLink
        $result=@{ok=$true;adapter=$AdapterGuid.ToString();route=$prefix;persistent=$true;gateway_changed=$false;motors_started=$false
            interface_index=$index;route_metric=$metric;route_instance=$instance}
        if($recovery -and $PreviousAdapterGuid -ne [Guid]::Empty){$result.previous_adapter=$PreviousAdapterGuid.ToString()}
        if($null -ne $temporaryContext){$result.temporary_address=$HostAddress+'/24'}
    } else {
        if(!$HostAddress -or !$TargetAddress -or $HostAddress -eq $TargetAddress){
            throw 'Choose distinct host and device addresses'
        }
        $before=@(($addresses+$savedAddresses) | Where-Object {$_.InterfaceIndex -eq $index})
        if($before|Where-Object {$_.IPAddress -notlike '169.254.*'}){throw 'The adapter configuration changed; refresh before setting up'}
        $dhcp=(Get-NetIPInterface -InterfaceIndex $index -AddressFamily IPv4).Dhcp
        # A down/virtual/Wi-Fi interface may still own the address selected before UAC.
        $conflicts=@(($addresses+$savedAddresses) | Where-Object { $_.IPAddress -eq $HostAddress })
        if($conflicts.Count -gt 0){throw 'The host address is already assigned to another adapter; refresh the network check'}
        if(($addresses+$savedAddresses) | Where-Object {$_.IPAddress -eq $TargetAddress}){throw 'The device address is assigned to this computer'}
        # Default store writes a persistent static address. No gateway or DNS edit.
        Confirm-Adapter -RequireLink
        $started=$true
        New-NetIPAddress -InterfaceIndex $index -IPAddress $HostAddress -PrefixLength 24 | Out-Null
        $added=$true
        $deadline=(Get-Date).AddSeconds(8)
        do {
            Start-Sleep -Milliseconds 400
            $ip=Get-NetIPAddress -InterfaceIndex $index -IPAddress $HostAddress -PolicyStore ActiveStore
        } while($ip.AddressState -eq 'Tentative' -and (Get-Date) -lt $deadline)
        if($ip.AddressState -ne 'Preferred' -or $ip.PrefixLength -ne 24){throw ('Address not usable: '+$ip.AddressState)}
        Confirm-Adapter -RequireLink
        $result=@{ok=$true;adapter=$AdapterGuid.ToString();address=($HostAddress+'/24');persistent=$true;gateway_changed=$false;motors_started=$false}
    }
} catch {
    $message=$_.Exception.Message
    $routeMayBeNew=$routeStarted -and $_.CategoryInfo.Category -ne 'ResourceExists'
    if($routeMayBeNew -and $oldRemovalStarted){
        # Removing the old active route may have completed before the old
        # persistent removal failed. Keep the proven persistent record intact
        # and allow a later attempt to recover directly from that store.
        $routeMayBeNew=$false
        try {
            Confirm-Scan -AllowCancelled
            Confirm-Adapter
            $previousMissing=Confirm-PreviousAdapter
            $oldActive=(Read-MigrationStore 'ActiveStore' $previousMissing $true).old.Count
            $oldPersistent=(Read-MigrationStore 'PersistentStore' $previousMissing $true).old.Count
            $routeMayBeNew=$oldPersistent -eq 1 -or ($observed -and $oldActive -eq 1)
        } catch {$message += '; Device route rollback unconfirmed: '+$_.Exception.Message}
    }
    $sameAdapter=$false
    $routeReverted=$false
    if($routeMayBeNew -or $started){
        # A cancellation can undo our own new route while this scan still owns
        # the marker. An expired or replaced owner cannot write even a rollback.
        try { if($automatic){Confirm-Scan -AllowCancelled}; Confirm-Adapter; $sameAdapter=$true }
        catch { $message += ' (rollback unconfirmed: '+$_.Exception.Message+')' }
    }
    if($sameAdapter -and $routeMayBeNew){
        $routeReverted=$true
        $matchesAttempt={ if($automatic){Test-ManagedRoute $_ $index}else{
            $_.InterfaceIndex -eq $index -and $_.NextHop -eq '0.0.0.0' -and $_.RouteMetric -eq $metric
        } }
        $rollbackStores=if($restore){@('ActiveStore')}else{@('PersistentStore','ActiveStore')}
        foreach($store in $rollbackStores){
            try {
                $created=@(Get-DeviceRoutes $store | Where-Object $matchesAttempt)
                if($created.Count -gt 1){throw 'More than one matching route'}
                if($created.Count -eq 1){
                    if($automatic){
                        Confirm-Scan -AllowCancelled
                        Confirm-Adapter
                        if($restore){$null=Confirm-RestorableRoute}
                        Remove-NetRoute -InputObject $created[0] -Confirm:$false -ErrorAction Stop
                    } else {
                        Remove-NetRoute -DestinationPrefix $prefix -InterfaceIndex $index -NextHop '0.0.0.0' -RouteMetric $metric -PolicyStore $store -Confirm:$false -ErrorAction Stop
                    }
                    if(@(Get-DeviceRoutes $store | Where-Object $matchesAttempt).Count){throw 'The route is still present'}
                }
            } catch {
                $routeReverted=$false
                $message += '; Device route rollback unconfirmed: '+$_.Exception.Message
            }
        }
    }
    if($sameAdapter -and $added){
        Remove-NetIPAddress -InterfaceIndex $index -IPAddress $HostAddress -Confirm:$false -ErrorAction SilentlyContinue
    }
    if($sameAdapter -and $started -and $dhcp -eq 'Enabled'){Set-NetIPInterface -InterfaceIndex $index -AddressFamily IPv4 -Dhcp Enabled -ErrorAction SilentlyContinue}
    if($null -ne $temporaryContext){
        try { Confirm-Scan -AllowCancelled; Remove-WujiAutomaticAddress $temporaryContext }
        catch {$message += '; Temporary address removal unconfirmed: '+$_.Exception.Message}
    }
    $result=@{ok=$false;error=$message;motors_started=$false}
    if($automatic -and $routeStarted -and !$routeReverted){$result.recovery_partial=$true}
}
$stream=[IO.File]::Open($ResultFile,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try{$bytes=[Text.Encoding]::UTF8.GetBytes(($result|ConvertTo-Json));$stream.Write($bytes,0,$bytes.Length)}finally{$stream.Dispose()}
