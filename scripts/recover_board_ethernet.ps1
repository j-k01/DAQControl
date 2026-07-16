<#
.SYNOPSIS
  Safely load the tracked A53 Ethernet app once, restore MicroBlaze, then test.

.DESCRIPTION
  Assumes the FPGA bitstream is already programmed. Unlike the old retry path,
  this never blindly stops a possibly working A53 for a second reload. It uses
  only tracked prebuilt ELFs, never an ignored local Vitis workspace artifact.

.EXAMPLE
  .\scripts\recover_board_ethernet.ps1 -LocalIp 192.168.2.1 -InterfaceAlias Ethernet
#>
[CmdletBinding()]
param(
    [string]$BoardIp = "192.168.2.10",
    [string]$LocalIp = "192.168.2.1",
    [string]$InterfaceAlias,
    [int]$CmdPort = 5006,
    [string]$Xsdb,
    [switch]$NoInit,
    [switch]$SkipDiagnostics
)
$ErrorActionPreference = "Stop"
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
    throw "xsdb not found. Pass -Xsdb <full path>."
}

$xsdbExe = Resolve-Xsdb
$psElf = (Resolve-Path "prebuilt\ps_eth_stream.elf").Path
$mbElf = (Resolve-Path "prebuilt\firmware.elf").Path
$ethArgs = @("load_ps_eth_stream.tcl", $psElf)
if (-not $NoInit) { $ethArgs += "--init-ps" }

Write-Host "Loading tracked A53 Ethernet ELF once:" -ForegroundColor Cyan
Write-Host "  $psElf"
& $xsdbExe @ethArgs
$ethExit = $LASTEXITCODE

# PS initialization may reset fabric-side processors. Always restore UART, but
# explicitly skip PS init so a successfully started A53 is not disturbed.
Write-Host "Restoring tracked MicroBlaze UART firmware:" -ForegroundColor Cyan
Write-Host "  $mbElf"
& $xsdbExe "load_mb_firmware.tcl" $mbElf "--no-ps-init"
$mbExit = $LASTEXITCODE
if ($mbExit -ne 0) { throw "MicroBlaze restore failed (exit $mbExit)." }
if ($ethExit -ne 0) {
    Write-Error ("A53 Ethernet load failed (exit $ethExit). If the error is " +
        "EDITR timeout, power-cycle the board and rerun this script once.")
    exit $ethExit
}

Write-Host "A53 loaded; waiting for PHY/autonegotiation..." -ForegroundColor Green
Start-Sleep -Seconds 5
if (-not $SkipDiagnostics) {
    $diagArgs = @("-BoardIp", $BoardIp, "-LocalIp", $LocalIp,
                  "-CmdPort", $CmdPort, "-Xsdb", $xsdbExe)
    if ($InterfaceAlias) { $diagArgs += @("-InterfaceAlias", $InterfaceAlias) }
    & "$root\scripts\diagnose_board_ethernet.ps1" @diagArgs
    exit $LASTEXITCODE
}
Write-Host "Recovery load complete. Run scripts\diagnose_board_ethernet.ps1." -ForegroundColor Green
