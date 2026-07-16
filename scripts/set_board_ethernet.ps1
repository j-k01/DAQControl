<#
.SYNOPSIS
  Configure one Windows Ethernet adapter for the direct DAQ-board link.

.DESCRIPTION
  FIRST snapshots the adapter's current internet configuration (DHCP/static,
  addresses, default routes, DNS mode, metric mode, MAC, and firewall-rule
  presence) to ProgramData. It then sets the adapter to 192.168.2.1/24 with NO
  gateway and adds an inbound UDP firewall rule restricted to board
  192.168.2.10.

  unset_board_ethernet.ps1 restores the snapshot after the normal network
  cable is reconnected. The backup is never silently overwritten. Other
  adapters are not modified. Self-elevates when necessary.

.EXAMPLE
  .\scripts\set_board_ethernet.ps1
.EXAMPLE
  .\scripts\set_board_ethernet.ps1 -InterfaceAlias "Ethernet 2"
#>
[CmdletBinding()]
param(
    [string]$InterfaceAlias = "Ethernet",
    [string]$LocalIp = "192.168.2.1",
    [int]$PrefixLength = 24,
    [string]$BoardIp = "192.168.2.10",
    [string]$BackupPath = "$env:ProgramData\DAQControl\board_ethernet_backup.json",
    [switch]$ReplaceBackup,
    [switch]$SkipTest
)
$ErrorActionPreference = "Stop"
$FwName = "DAQ board (UDP in)"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Test-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltinRole]::Administrator)
}

if (-not (Test-Administrator)) {
    Write-Host "Requesting Administrator permission to configure '$InterfaceAlias'..." -ForegroundColor Yellow
    $argLine = ("-NoProfile -ExecutionPolicy Bypass -File `"{0}`" " +
                "-InterfaceAlias `"{1}`" -LocalIp {2} -PrefixLength {3} " +
                "-BoardIp {4} -BackupPath `"{5}`"") -f `
                $PSCommandPath, $InterfaceAlias, $LocalIp, $PrefixLength, $BoardIp, $BackupPath
    if ($ReplaceBackup) { $argLine += " -ReplaceBackup" }
    if ($SkipTest) { $argLine += " -SkipTest" }
    $proc = Start-Process powershell.exe -Verb RunAs -ArgumentList $argLine -Wait -PassThru
    exit $proc.ExitCode
}

$adapter = Get-NetAdapter -Name $InterfaceAlias -ErrorAction SilentlyContinue
if (-not $adapter) {
    Write-Host "Adapter '$InterfaceAlias' was not found. Available adapters:" -ForegroundColor Red
    Get-NetAdapter | Select-Object Name,Status,LinkSpeed,InterfaceDescription |
        Format-Table -AutoSize
    exit 2
}

$conflict = Get-NetIPAddress -IPAddress $LocalIp -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.InterfaceIndex -ne $adapter.ifIndex }
if ($conflict) {
    $other = (Get-NetAdapter -InterfaceIndex $conflict.InterfaceIndex).Name
    Write-Error "$LocalIp is already assigned to another adapter ('$other'). Remove that conflict first."
    exit 2
}

$existing = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 `
    -IPAddress $LocalIp -ErrorAction SilentlyContinue
$reuseBackup = $false
if (Test-Path $BackupPath) {
    $saved = Get-Content $BackupPath -Raw | ConvertFrom-Json
    if ($saved.MacAddress -ne $adapter.MacAddress) {
        Write-Error ("Existing backup belongs to MAC $($saved.MacAddress), not " +
            "'$InterfaceAlias' ($($adapter.MacAddress)). Refusing to overwrite it.")
        exit 3
    }
    if ($existing -and $existing.PrefixLength -eq $PrefixLength) {
        $reuseBackup = $true
        Write-Host "Direct-link configuration and its original backup already exist." -ForegroundColor Green
    } elseif (-not $ReplaceBackup) {
        Write-Error ("Backup already exists at '$BackupPath', but the adapter is not " +
            "currently configured for the board. Restore it with unset_board_ethernet.ps1, " +
            "or explicitly replace it with -ReplaceBackup.")
        exit 3
    }
} elseif ($existing) {
    Write-Error ("$LocalIp is already assigned but no original internet backup exists. " +
        "Refusing to pretend the current board configuration is the original state.")
    exit 3
}

if (-not $reuseBackup) {
    $ipIf = Get-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4
    $addresses = @(Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 `
        -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike "169.254.*" })
    $routes = @(Get-NetRoute -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 `
        -ErrorAction SilentlyContinue | Where-Object { $_.DestinationPrefix -eq "0.0.0.0/0" })
    $dns = @((Get-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex `
        -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses)
    $interfaceGuid = ([string]$adapter.InterfaceGuid).Trim("{}")
    $interfaceRegistryPath = "HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\{$interfaceGuid}"
    $staticDns = (Get-ItemProperty -LiteralPath $interfaceRegistryPath `
        -Name NameServer -ErrorAction SilentlyContinue).NameServer
    $state = [ordered]@{
        Version = 2
        CreatedUtc = (Get-Date).ToUniversalTime().ToString("o")
        InterfaceAlias = $adapter.Name
        InterfaceDescription = $adapter.InterfaceDescription
        MacAddress = $adapter.MacAddress
        WasDhcp = ($ipIf.Dhcp -eq "Enabled")
        AutomaticMetric = ($ipIf.AutomaticMetric -eq "Enabled")
        InterfaceMetric = $ipIf.InterfaceMetric
        Addresses = @($addresses | ForEach-Object {
            [ordered]@{ IPAddress=$_.IPAddress; PrefixLength=$_.PrefixLength;
                        PrefixOrigin="$($_.PrefixOrigin)" }
        })
        DefaultRoutes = @($routes | ForEach-Object {
            [ordered]@{ NextHop=$_.NextHop; RouteMetric=$_.RouteMetric }
        })
        DnsWasAutomatic = [string]::IsNullOrWhiteSpace([string]$staticDns)
        DnsServers = @($dns)
        FirewallRuleExisted = [bool](Get-NetFirewallRule -DisplayName $FwName `
            -ErrorAction SilentlyContinue)
    }
    $backupDir = Split-Path -Parent $BackupPath
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $state | ConvertTo-Json -Depth 6 | Set-Content -Path $BackupPath -Encoding UTF8
    Write-Host "Saved original internet configuration:" -ForegroundColor Green
    Write-Host "  $BackupPath"
    $mode = if ($state.WasDhcp) { "DHCP" } else { "static" }
    $savedIps = @($state.Addresses | ForEach-Object { "$($_.IPAddress)/$($_.PrefixLength)" }) -join ", "
    $savedGateways = @($state.DefaultRoutes | ForEach-Object { $_.NextHop }) -join ", "
    Write-Host "  Mode: $mode; IP: $savedIps; gateway: $savedGateways"

    # The direct link must have one deterministic address and no internet
    # default route. All removed state is captured above for exact restoration.
    Set-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -Dhcp Disabled
    Get-NetRoute -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 `
        -ErrorAction SilentlyContinue |
        Where-Object { $_.DestinationPrefix -eq "0.0.0.0/0" } |
        Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
    Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 `
        -ErrorAction SilentlyContinue |
        Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
    New-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress $LocalIp `
        -PrefixLength $PrefixLength -AddressFamily IPv4 | Out-Null
    Write-Host "Assigned direct-link address $LocalIp/$PrefixLength with no gateway." -ForegroundColor Green
} else {
    Write-Host "Preserving original backup: $BackupPath"
}

if (-not (Get-NetFirewallRule -DisplayName $FwName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $FwName -Direction Inbound -Protocol UDP `
        -RemoteAddress $BoardIp -Action Allow -Profile Any | Out-Null
    Write-Host "Added firewall rule '$FwName' for UDP from $BoardIp." -ForegroundColor Green
} else {
    Write-Host "Firewall rule '$FwName' already exists."
}

$adapter = Get-NetAdapter -Name $InterfaceAlias
Write-Host "`nDirect-link configuration:" -ForegroundColor Cyan
$adapter | Select-Object Name,Status,LinkSpeed,MacAddress | Format-Table -AutoSize
Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 |
    Select-Object IPAddress,PrefixLength,PrefixOrigin | Format-Table -AutoSize
if ($adapter.Status -ne "Up") {
    Write-Warning "Adapter link is not Up; check the cable and ZCU102 GEM3 RJ45 port."
}

if (-not $SkipTest) {
    Write-Host "Running DAQ UDP PING/PONG test..." -ForegroundColor Cyan
    & python "$root\scripts\test_board_ethernet.py" --board-ip $BoardIp --local-ip $LocalIp
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Host networking is configured, but the board did not answer UDP. Run diagnose_board_ethernet.ps1 next."
    }
}

Write-Host "`nTo undo only this configuration:" -ForegroundColor Cyan
Write-Host "  1. Disconnect the FPGA cable and reconnect the normal network cable."
Write-Host "  2. powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\unset_board_ethernet.ps1 -InterfaceAlias `"$InterfaceAlias`""
