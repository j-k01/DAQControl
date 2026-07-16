<#
.SYNOPSIS
  Remove the direct DAQ-board Ethernet configuration from one adapter.

.DESCRIPTION
  Restores the exact adapter snapshot saved by set_board_ethernet.ps1:
  DHCP/static mode, manual IPv4 addresses, default routes, automatic/static
  DNS, automatic/manual interface metric, and prior firewall-rule presence.
  The backup is verified against the adapter MAC before use and removed only
  after a successful restore. Other adapters are not modified. Self-elevates
  when necessary.

  If the backup was lost, -AssumeDhcp provides an explicit emergency fallback.

.EXAMPLE
  .\scripts\unset_board_ethernet.ps1
.EXAMPLE
  .\scripts\unset_board_ethernet.ps1 -InterfaceAlias "Ethernet 2"
#>
[CmdletBinding()]
param(
    [string]$InterfaceAlias = "Ethernet",
    [string]$LocalIp = "192.168.2.1",
    [string]$BackupPath = "$env:ProgramData\DAQControl\board_ethernet_backup.json",
    [switch]$AssumeDhcp,
    [switch]$KeepBackup
)
$ErrorActionPreference = "Stop"
$FwName = "DAQ board (UDP in)"

function Test-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltinRole]::Administrator)
}

if (-not (Test-Administrator)) {
    Write-Host "Requesting Administrator permission to restore '$InterfaceAlias'..." -ForegroundColor Yellow
    $argLine = ("-NoProfile -ExecutionPolicy Bypass -File `"{0}`" " +
                "-InterfaceAlias `"{1}`" -LocalIp {2} -BackupPath `"{3}`"") -f `
                $PSCommandPath, $InterfaceAlias, $LocalIp, $BackupPath
    if ($AssumeDhcp) { $argLine += " -AssumeDhcp" }
    if ($KeepBackup) { $argLine += " -KeepBackup" }
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

$saved = $null
if (Test-Path $BackupPath) {
    $saved = Get-Content $BackupPath -Raw | ConvertFrom-Json
    if (-not $saved.Version -or [int]$saved.Version -lt 2) {
        Write-Error "Backup '$BackupPath' uses an unsupported format. Refusing to guess."
        exit 3
    }
    if ($saved.MacAddress -ne $adapter.MacAddress) {
        Write-Error ("Backup belongs to MAC $($saved.MacAddress), not '$InterfaceAlias' " +
            "($($adapter.MacAddress)). Refusing to apply it.")
        exit 3
    }
} elseif (-not $AssumeDhcp) {
    Write-Error ("Original internet backup '$BackupPath' does not exist. Refusing " +
        "to guess. If this adapter originally used DHCP, rerun with -AssumeDhcp.")
    exit 3
}

# Remove only this adapter's current board-mode addresses/routes before restore.
Set-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -Dhcp Disabled
Get-NetRoute -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 `
    -ErrorAction SilentlyContinue |
    Where-Object { $_.DestinationPrefix -eq "0.0.0.0/0" } |
    Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 `
    -ErrorAction SilentlyContinue |
    Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue

if ($saved) {
    if ([bool]$saved.AutomaticMetric) {
        Set-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 `
            -AutomaticMetric Enabled
    } else {
        Set-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 `
            -AutomaticMetric Disabled -InterfaceMetric ([int]$saved.InterfaceMetric)
    }
    if ([bool]$saved.WasDhcp) {
        Set-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -Dhcp Enabled
        Write-Host "Restored DHCP on '$InterfaceAlias'." -ForegroundColor Green
    } else {
        foreach ($addr in @($saved.Addresses)) {
            if ($addr.IPAddress -and $addr.IPAddress -notlike "169.254.*") {
                New-NetIPAddress -InterfaceIndex $adapter.ifIndex `
                    -IPAddress $addr.IPAddress -PrefixLength ([int]$addr.PrefixLength) `
                    -AddressFamily IPv4 | Out-Null
            }
        }
        foreach ($route in @($saved.DefaultRoutes)) {
            if ($route.NextHop -and $route.NextHop -ne "0.0.0.0") {
                New-NetRoute -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 `
                    -DestinationPrefix "0.0.0.0/0" -NextHop $route.NextHop `
                    -RouteMetric ([int]$route.RouteMetric) | Out-Null
            }
        }
        Write-Host "Restored saved static IPv4 and default route configuration." -ForegroundColor Green
    }

    $dns = @($saved.DnsServers | Where-Object { $_ })
    if ([bool]$saved.DnsWasAutomatic) {
        Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ResetServerAddresses
        Write-Host "Restored automatic DNS."
    } elseif ($dns.Count) {
        Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ServerAddresses $dns
        Write-Host "Restored saved static DNS servers: $($dns -join ', ')"
    } else {
        Write-Warning "The backup says DNS was static but contains no servers; DNS was left unchanged."
    }

    if (-not [bool]$saved.FirewallRuleExisted) {
        Get-NetFirewallRule -DisplayName $FwName -ErrorAction SilentlyContinue |
            Remove-NetFirewallRule -ErrorAction SilentlyContinue
        Write-Host "Removed firewall rule created for the DAQ board."
    } else {
        Write-Host "Preserved pre-existing firewall rule '$FwName'."
    }
    if (-not $KeepBackup) {
        Remove-Item -LiteralPath $BackupPath -Force
        Write-Host "Consumed and removed restored backup: $BackupPath"
    }
} else {
    # Explicit recovery only; never reached without the user's -AssumeDhcp.
    Set-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -Dhcp Enabled
    Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ResetServerAddresses
    Get-NetFirewallRule -DisplayName $FwName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
    Write-Warning "No backup existed; restored DHCP/DNS by explicit assumption."
}

Write-Host "`nRemaining IPv4 configuration on '$InterfaceAlias':" -ForegroundColor Cyan
Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 `
    -ErrorAction SilentlyContinue |
    Select-Object IPAddress,PrefixLength,PrefixOrigin | Format-Table -AutoSize
Write-Host "Reconnect the normal network cable; Windows will reacquire DHCP automatically when applicable." -ForegroundColor Cyan
