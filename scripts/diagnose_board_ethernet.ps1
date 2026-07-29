<#
.SYNOPSIS
  Read-only diagnosis of the direct PC <-> DAQ-board Ethernet path.

.DESCRIPTION
  Checks host link/IP/route, ICMP, ARP, the real DAQ UDP PING/PONG command
  path, and the unified Linux DAQ-service state over the PS UART.
  It does not program, reset, or restart anything.

.EXAMPLE
  .\scripts\diagnose_board_ethernet.ps1
.EXAMPLE
  .\scripts\diagnose_board_ethernet.ps1 -InterfaceAlias "Ethernet" -LocalIp 192.168.2.1
#>
[CmdletBinding()]
param(
    [string]$BoardIp = "192.168.2.10",
    [string]$LocalIp,
    [string]$InterfaceAlias,
    [int]$CmdPort = 5006,
    [string]$PsPort = "COM9",
    [string]$Python = "python",
    # Backward-compatible, ignored legacy parameter.
    [string]$Xsdb,
    [Alias("SkipJtag")]
    [switch]$SkipSerial
)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

Write-Host "=== DAQ Ethernet diagnosis (read-only) ===" -ForegroundColor Cyan
Write-Host "Board: $BoardIp/24, UDP command port $CmdPort"
try { Write-Host "Repository: $(git rev-parse --short HEAD)" } catch {}

$adapters = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue)
Write-Host "`n[1] Physical adapters"
if ($adapters.Count) {
    $adapters | Select-Object Name,Status,LinkSpeed,MacAddress,InterfaceDescription |
        Format-Table -AutoSize
} else {
    Write-Warning "No physical adapters were returned by Get-NetAdapter."
}

if ($InterfaceAlias) {
    $adapter = Get-NetAdapter -Name $InterfaceAlias -ErrorAction SilentlyContinue
} elseif ($LocalIp) {
    $ipObj = Get-NetIPAddress -IPAddress $LocalIp -AddressFamily IPv4 -ErrorAction SilentlyContinue
    $adapter = if ($ipObj) { Get-NetAdapter -InterfaceIndex $ipObj.InterfaceIndex -ErrorAction SilentlyContinue }
} else {
    $ipObj = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -like "192.168.2.*" -and $_.IPAddress -ne $BoardIp } |
        Select-Object -First 1
    $adapter = if ($ipObj) { Get-NetAdapter -InterfaceIndex $ipObj.InterfaceIndex -ErrorAction SilentlyContinue }
}

if (-not $LocalIp -and $adapter) {
    $ipObj = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex `
        -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -like "192.168.2.*" -and
            $_.IPAddress -ne $BoardIp
        } |
        Select-Object -First 1
    if ($ipObj) { $LocalIp = $ipObj.IPAddress }
}
if (-not $adapter) {
    Write-Warning "Could not identify the direct-link adapter. Pass -InterfaceAlias and -LocalIp."
} else {
    Write-Host "Selected adapter: $($adapter.Name) [$($adapter.Status), $($adapter.LinkSpeed)]"
    $ips = @(Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue)
    $ips | Select-Object IPAddress,PrefixLength,PrefixOrigin | Format-Table -AutoSize
    if ($adapter.Status -ne "Up") { Write-Warning "LINK DOWN: check cable and ZCU102 GEM3 RJ45 LEDs." }
}

Write-Host "[2] Host subnet and route"
if (-not $LocalIp) {
    Write-Warning "No host 192.168.2.x address found. Assign one, e.g. 192.168.2.1/24."
} elseif ($LocalIp -eq $BoardIp) {
    Write-Warning "Host and board cannot both use $BoardIp."
} else {
    Write-Host "Host source IP: $LocalIp"
}
Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.DestinationPrefix -eq "192.168.2.0/24" } |
    Select-Object DestinationPrefix,InterfaceAlias,NextHop,RouteMetric |
    Format-Table -AutoSize

Write-Host "[3] ICMP and ARP"
$icmp = Test-Connection -ComputerName $BoardIp -Count 2 -Quiet -ErrorAction SilentlyContinue
Write-Host ("ICMP ping: " + $(if ($icmp) { "PASS" } else { "FAIL" }))
$arpLines = @((arp -a) | Select-String ([regex]::Escape($BoardIp)))
if ($arpLines.Count) {
    Write-Host "ARP entry: $($arpLines -join '; ')"
} else {
    Write-Host "ARP entry: none"
}

Write-Host "`n[4] DAQ UDP service PING/PONG"
$pyArgs = @("scripts\test_board_ethernet.py", "--board-ip", $BoardIp,
            "--cmd-port", "$CmdPort")
if ($LocalIp) { $pyArgs += @("--local-ip", $LocalIp) }
& $Python @pyArgs
$udpExit = $LASTEXITCODE

$serialExit = -1
if (-not $SkipSerial) {
    Write-Host "`n[5] Unified Linux DAQ-service state over $PsPort"
    & $Python "scripts\recover_linux_daq_service.py" "--port" $PsPort "--probe"
    $serialExit = $LASTEXITCODE
}
if ($Xsdb) {
    Write-Warning ("-Xsdb is ignored: halting A53 core 0 for a mailbox read is " +
        "not safe or necessary under the unified SMP Linux runtime.")
}

Write-Host "`n=== Interpretation ===" -ForegroundColor Cyan
if ($udpExit -eq 0) {
    Write-Host "PASS: Ethernet is operational end-to-end." -ForegroundColor Green
    exit 0
}
if ($serialExit -eq 0) {
    if ($icmp -or $arpLines.Count) {
        Write-Warning ("Linux and daq-eth-service are alive, but UDP PONG failed: " +
            "check Windows Firewall/VPN and source-IP selection.")
    } else {
        Write-Warning ("The service is running, but the board is absent from the " +
            "host network: check the adapter, 192.168.2.x/24 assignment, cable, " +
            "and GEM3 port.")
    }
} elseif ($serialExit -gt 0) {
    if ($icmp) {
        Write-Warning ("Linux networking answers ICMP, but the DAQ service could " +
            "not be confirmed. Run recover_ethernet.cmd to restart only the service.")
    } else {
        Write-Warning ("The unified Linux shell/service was not confirmed. Run " +
            "recover_ethernet.cmd for bounded recovery.")
    }
} else {
    Write-Warning "UDP failed and Linux service state was not measured."
}
exit 1
