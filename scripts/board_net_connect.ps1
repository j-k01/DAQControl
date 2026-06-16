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
    [string]$IPAddress      = "192.168.2.1",
    [int]   $PrefixLength   = 24
)

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

$existing = Get-NetIPAddress -IPAddress $IPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "$IPAddress is already configured -- nothing to do." -ForegroundColor Yellow
} else {
    New-NetIPAddress -InterfaceAlias $InterfaceAlias -IPAddress $IPAddress `
        -PrefixLength $PrefixLength -ErrorAction Stop | Out-Null
    Write-Host "Added $IPAddress/$PrefixLength to '$InterfaceAlias' as a second IP." -ForegroundColor Green
    Write-Host "DHCP / internet are unchanged."
}

Write-Host ""
Write-Host "IPv4 addresses on '$InterfaceAlias':"
Get-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 |
    Select-Object IPAddress, PrefixLength, PrefixOrigin | Format-Table -AutoSize
Write-Host "Find the board:  python scripts\find_daq_eth.py --target 192.168.2.10" -ForegroundColor Cyan
Write-Host "Undo this:       .\scripts\board_net_revert.ps1"
