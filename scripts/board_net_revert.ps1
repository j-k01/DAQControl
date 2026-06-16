<#
.SYNOPSIS
  Revert the board-Ethernet change and restore normal internet settings.

  Removes the 192.168.2.1 address that board_net_connect.ps1 added and makes sure
  the Ethernet adapter is back on DHCP (automatic IP + DNS), so internet works
  exactly as before. Safe to run even if the connect script was never used.

.EXAMPLE
  .\scripts\board_net_revert.ps1
.EXAMPLE
  .\scripts\board_net_revert.ps1 -InterfaceAlias "Wi-Fi"
#>
[CmdletBinding()]
param(
    [string]$InterfaceAlias = "Ethernet",
    [string]$IPAddress      = "192.168.2.1"
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
    Write-Error "Re-run with -InterfaceAlias '<Name>'."
    exit 1
}

Write-Host "Reverting '$InterfaceAlias' to normal (DHCP) settings..."

# 1. remove any manually-added 192.168.2.x we put on this adapter (leaves any
#    DHCP-assigned address on that subnet intact -- only Manual ones are removed)
Get-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 -PrefixOrigin Manual -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -like '192.168.2.*' } |
    Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue

# also remove the board's firewall rule added by board_net_connect.ps1
Get-NetFirewallRule -DisplayName "DAQ board (UDP in)" -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue

# 2. make sure IP + DNS are back on automatic (DHCP)
Set-NetIPInterface -InterfaceAlias $InterfaceAlias -Dhcp Enabled
Set-DnsClientServerAddress -InterfaceAlias $InterfaceAlias -ResetServerAddresses

# 3. pull a fresh lease
ipconfig /renew | Out-Null

Write-Host "Done. '$InterfaceAlias' is back on DHCP." -ForegroundColor Green
Write-Host ""
Write-Host "IPv4 addresses on '$InterfaceAlias':"
Get-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 |
    Select-Object IPAddress, PrefixLength, PrefixOrigin | Format-Table -AutoSize
