<#
.SYNOPSIS
  Connect this PC to the DAQ board over Ethernet WITHOUT breaking internet.

  The board is static 192.168.2.10/24. This adds 192.168.2.1/24 to the Ethernet
  adapter as a SECOND IP address -- it does NOT touch DHCP, so your normal
  internet IP, gateway, and DNS are left exactly as they were. Undo it with
  scripts\board_net_revert.ps1.

.EXAMPLE
  .\scripts\board_net_connect.ps1
.EXAMPLE
  .\scripts\board_net_connect.ps1 -InterfaceAlias "Wi-Fi"
#>
[CmdletBinding()]
param(
    [string]$InterfaceAlias = "Ethernet",
    [string]$IPAddress      = "192.168.2.50",   # NOT .1 (that's usually the gateway) or .10 (the board)
    [int]   $PrefixLength   = 24,
    [string]$BoardIP        = "192.168.2.10"
)
$FwName = "DAQ board (UDP in)"

$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    Write-Error "Run this in an Administrator PowerShell (right-click PowerShell > Run as administrator)."
    exit 1
}

if (-not (Get-NetAdapter -Name $InterfaceAlias -ErrorAction SilentlyContinue)) {
    Write-Host "Adapter '$InterfaceAlias' not found. Adapters on this PC:" -ForegroundColor Yellow
    Get-NetAdapter | Format-Table Name, Status -AutoSize
    Write-Error "Re-run with -InterfaceAlias '<Name>' (use a name from the list above)."
    exit 1
}

# Reach 192.168.2.0/24 without disturbing internet:
#  - if this adapter already has a 192.168.2.x address (router is on that subnet,
#    or DHCP gave us one), the board is reachable directly -- add nothing.
#  - never use the default gateway's IP (that's what broke internet before).
$already = Get-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 -ErrorAction SilentlyContinue |
           Where-Object { $_.IPAddress -like '192.168.2.*' } | Select-Object -First 1
$gw = (Get-NetIPConfiguration -InterfaceAlias $InterfaceAlias -ErrorAction SilentlyContinue).IPv4DefaultGateway.NextHop

if ($already) {
    $LocalIP = $already.IPAddress
    Write-Host "'$InterfaceAlias' already has $LocalIP on 192.168.2.0/24 -- board reachable directly." -ForegroundColor Green
    Write-Host "Not adding an IP (and not touching the gateway)."
} elseif ($IPAddress -eq $gw) {
    Write-Error "$IPAddress is this network's gateway -- using it would break internet. Re-run with -IPAddress 192.168.2.51"
    exit 1
} else {
    New-NetIPAddress -InterfaceAlias $InterfaceAlias -IPAddress $IPAddress `
        -PrefixLength $PrefixLength -ErrorAction Stop | Out-Null
    $LocalIP = $IPAddress
    Write-Host "Added $IPAddress/$PrefixLength to '$InterfaceAlias' as a second IP (DHCP/internet unchanged)." -ForegroundColor Green
}

# Allow the board's UDP replies through Windows Firewall. The board answers from
# a different source port than 5006, so Windows' stateful firewall treats the
# reply (PONG / data) as unsolicited inbound and drops it. Allow inbound UDP
# from the board's IP so PONG, the burst readout, and the live stream get in.
if (-not (Get-NetFirewallRule -DisplayName $FwName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $FwName -Direction Inbound -Protocol UDP `
        -RemoteAddress $BoardIP -Action Allow -Profile Any | Out-Null
    Write-Host "Added firewall rule '$FwName' (allow inbound UDP from $BoardIP)." -ForegroundColor Green
} else {
    Write-Host "Firewall rule '$FwName' already present." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "IPv4 addresses on '$InterfaceAlias':"
Get-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 |
    Select-Object IPAddress, PrefixLength, PrefixOrigin | Format-Table -AutoSize
Write-Host "Find the board:  python scripts\find_daq_eth.py --target 192.168.2.10" -ForegroundColor Cyan
Write-Host "Use the GUI:     python scripts\dac_scope_qt.py --port COMx --board-ip 192.168.2.10 --local-ip $LocalIP" -ForegroundColor Cyan
Write-Host "Undo this:       .\scripts\board_net_revert.ps1"
