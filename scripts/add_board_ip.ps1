<#
.SYNOPSIS
  Reach the DAQ board over Ethernet by adding a SECOND IP on the board's subnet
  (192.168.2.0/24) without disturbing DHCP / internet. Fully reversible.

  The board is static 192.168.2.10/24 (no DHCP). On a shared router that hands
  your PC a different subnet, you just need a 192.168.2.x address on the adapter
  that reaches the board. This adds it as an *additional* address, so the
  DHCP-assigned internet address, gateway, and DNS are left intact.

.EXAMPLE
  # add 192.168.2.1/24 to the internet-connected adapter (keeps DHCP/internet)
  powershell -ExecutionPolicy Bypass -File scripts\add_board_ip.ps1

.EXAMPLE
  # pick the adapter explicitly
  powershell -ExecutionPolicy Bypass -File scripts\add_board_ip.ps1 -InterfaceAlias "Ethernet"

.EXAMPLE
  # undo: remove the board IP (DHCP/internet untouched)
  powershell -ExecutionPolicy Bypass -File scripts\add_board_ip.ps1 -Remove

.EXAMPLE
  # recover a NIC that was switched to static and lost internet (re-enable DHCP+DNS)
  powershell -ExecutionPolicy Bypass -File scripts\add_board_ip.ps1 -RestoreDhcp -InterfaceAlias "Ethernet"
#>
[CmdletBinding()]
param(
    [string]$InterfaceAlias,
    [string]$IPAddress = "192.168.2.1",
    [int]$PrefixLength = 24,
    [switch]$Remove,
    [switch]$RestoreDhcp
)

# --- must be Administrator ---------------------------------------------------
$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    Write-Error "Run this in an Administrator PowerShell (right-click > Run as administrator)."
    exit 1
}

function Get-InternetAdapter {
    # the adapter carrying the active default route = the one on the router
    $r = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
         Where-Object { $_.NextHop -ne '0.0.0.0' } |
         Sort-Object RouteMetric | Select-Object -First 1
    if ($r) { return (Get-NetAdapter -InterfaceIndex $r.ifIndex -ErrorAction SilentlyContinue).Name }
    return $null
}

# --- resolve the target adapter ----------------------------------------------
if (-not $InterfaceAlias) {
    if ($Remove -or $RestoreDhcp) {
        $cur = Get-NetIPAddress -IPAddress $IPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue
        if ($cur) { $InterfaceAlias = (Get-NetAdapter -InterfaceIndex $cur.InterfaceIndex).Name }
    }
    if (-not $InterfaceAlias) { $InterfaceAlias = Get-InternetAdapter }
}
if (-not $InterfaceAlias) {
    Write-Host "Could not auto-pick an adapter. Adapters that are Up:" -ForegroundColor Yellow
    Get-NetAdapter | Where-Object Status -eq 'Up' |
        Format-Table Name, InterfaceDescription, Status -AutoSize
    Write-Error "Re-run with -InterfaceAlias '<Name>'."
    exit 1
}
Write-Host "Adapter: $InterfaceAlias" -ForegroundColor Cyan

# --- recover a NIC that was switched to static (lost internet) ----------------
if ($RestoreDhcp) {
    Write-Host "Restoring DHCP + DNS on '$InterfaceAlias'..."
    Get-NetIPAddress -InterfaceAlias $InterfaceAlias -IPAddress $IPAddress -ErrorAction SilentlyContinue |
        Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
    Set-NetIPInterface -InterfaceAlias $InterfaceAlias -Dhcp Enabled
    Set-DnsClientServerAddress -InterfaceAlias $InterfaceAlias -ResetServerAddresses
    ipconfig /release "$InterfaceAlias" | Out-Null
    ipconfig /renew "$InterfaceAlias"   | Out-Null
    Write-Host "Done. Internet should be back (adapter is back on DHCP)." -ForegroundColor Green
    exit 0
}

# --- remove the board IP (leave DHCP/internet alone) --------------------------
if ($Remove) {
    Write-Host "Removing $IPAddress from '$InterfaceAlias' (DHCP/internet untouched)..."
    Get-NetIPAddress -InterfaceAlias $InterfaceAlias -IPAddress $IPAddress -ErrorAction SilentlyContinue |
        Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed." -ForegroundColor Green
    exit 0
}

# --- default: ADD the board IP as a second address (keep DHCP/internet) -------
$dup = Get-NetIPAddress -IPAddress $IPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue
if ($dup) {
    $on = (Get-NetAdapter -InterfaceIndex $dup.InterfaceIndex).Name
    Write-Host "$IPAddress is already present on '$on'. Nothing to do." -ForegroundColor Yellow
} else {
    New-NetIPAddress -InterfaceAlias $InterfaceAlias -IPAddress $IPAddress `
        -PrefixLength $PrefixLength -ErrorAction Stop | Out-Null
    Write-Host "Added $IPAddress/$PrefixLength to '$InterfaceAlias' as a second IP." -ForegroundColor Green
    Write-Host "DHCP address, gateway, and DNS are unchanged -- internet stays up."
}

Write-Host ""
Write-Host "IPv4 addresses on '$InterfaceAlias':"
Get-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 |
    Select-Object IPAddress, PrefixLength, PrefixOrigin | Format-Table -AutoSize
Write-Host "Next: python scripts\find_daq_eth.py --target 192.168.2.10" -ForegroundColor Cyan
Write-Host "Undo: powershell -ExecutionPolicy Bypass -File scripts\add_board_ip.ps1 -Remove"
