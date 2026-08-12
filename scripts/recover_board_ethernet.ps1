<#
.SYNOPSIS
  Compatibility entry point for unified Linux DAQ Ethernet recovery.

.DESCRIPTION
  The former implementation reloaded the bare-metal A53 Ethernet ELF. That
  would terminate the unified Linux runtime and Pico USB host, so this wrapper
  now delegates to the bounded root recovery ladder:

    host path -> Linux service restart over COM9 -> unified runtime reload.

.EXAMPLE
  .\scripts\recover_board_ethernet.ps1 -LocalIp 192.168.2.1 -InterfaceAlias Ethernet
#>
[CmdletBinding()]
param(
    [string]$BoardIp = "192.168.2.10",
    [string]$LocalIp = "192.168.2.1",
    [string]$InterfaceAlias = "Ethernet",
    [int]$CmdPort = 5006,
    [string]$PsPort = "COM9",
    [string]$Python,
    [string]$Remote,
    [string]$Identity,
    [switch]$SkipDiagnostics,
    # Accepted only so old command lines fail safely rather than loading the
    # retired bare-metal application.
    [switch]$NoInit,
    [string]$Xsdb
)

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$arguments = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $root "recover_ethernet.ps1"),
    "-BoardIp", $BoardIp,
    "-LocalIp", $LocalIp,
    "-InterfaceAlias", $InterfaceAlias,
    "-CmdPort", "$CmdPort",
    "-PsPort", $PsPort
)
if ($Python) { $arguments += @("-Python", $Python) }
if ($Remote) { $arguments += @("-Remote", $Remote) }
if ($Identity) { $arguments += @("-Identity", $Identity) }

if ($NoInit -or $Xsdb) {
    Write-Warning ("-NoInit/-Xsdb are obsolete under the unified Linux runtime " +
        "and were ignored.")
}

& powershell.exe @arguments
exit $LASTEXITCODE
