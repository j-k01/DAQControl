<#
.SYNOPSIS
  Read-only diagnosis of the direct PC <-> DAQ-board Ethernet path.

.DESCRIPTION
  Checks host link/IP/route, ICMP, ARP, the real A53 UDP PING/PONG command
  path, and (when xsdb is available) the A53 progress mailbox + heartbeat.
  It does not program or reset the FPGA, MicroBlaze, or A53.

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
    [string]$Xsdb,
    [switch]$SkipJtag
)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

function Resolve-Xsdb {
    if ($Xsdb -and (Test-Path $Xsdb)) { return (Resolve-Path $Xsdb).Path }
    $candidates = @(
        Get-ChildItem "C:\Xilinx\*\Vivado\bin\xsdb.bat" -ErrorAction SilentlyContinue
        Get-ChildItem "C:\Xilinx\Vivado\*\bin\xsdb.bat" -ErrorAction SilentlyContinue
        Get-ChildItem "C:\Xilinx\*\Vitis\bin\xsdb.bat" -ErrorAction SilentlyContinue
    ) | Sort-Object FullName -Descending
    if ($candidates.Count) { return $candidates[0].FullName }
    $cmd = Get-Command xsdb.bat,xsdb -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    return $null
}

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

if (-not $LocalIp -and $ipObj) { $LocalIp = $ipObj.IPAddress }
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

Write-Host "`n[4] A53 UDP application PING/PONG"
$pyArgs = @("scripts\test_board_ethernet.py", "--board-ip", $BoardIp,
            "--cmd-port", "$CmdPort")
if ($LocalIp) { $pyArgs += @("--local-ip", $LocalIp) }
& python @pyArgs
$udpExit = $LASTEXITCODE

$jtagExit = -1
if (-not $SkipJtag) {
    Write-Host "`n[5] A53 mailbox/heartbeat over JTAG"
    $xsdbExe = Resolve-Xsdb
    if ($xsdbExe) {
        Write-Host "XSDB: $xsdbExe"
        & $xsdbExe "scripts\test_eth_mailbox.tcl"
        $jtagExit = $LASTEXITCODE
    } else {
        Write-Warning "xsdb not found; rerun with -Xsdb <full path> or -SkipJtag."
    }
}

Write-Host "`n=== Interpretation ===" -ForegroundColor Cyan
if ($udpExit -eq 0) {
    Write-Host "PASS: Ethernet is operational end-to-end." -ForegroundColor Green
    exit 0
}
if ($jtagExit -eq 0) {
    if ($arpLines.Count) {
        Write-Warning "A53 is alive and L2/ARP works, but UDP PONG failed: check Windows Firewall/VPN and source-IP selection."
    } else {
        Write-Warning "A53 is alive but the board is absent from ARP: check direct-link adapter, 192.168.2.x/24 assignment, cable, and GEM3 port."
    }
} elseif ($jtagExit -gt 0) {
    Write-Warning "A53 mailbox test failed: the PS Ethernet application is not running correctly. Run recover_board_ethernet.ps1."
} else {
    Write-Warning "UDP failed and A53 state was not measured. Run again with working XSDB for a definitive diagnosis."
}
exit 1
