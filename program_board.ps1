<#
.SYNOPSIS
  Program the ZCU102 from PowerShell: FPGA bitstream + MicroBlaze firmware, then
  the A53 PS-Ethernet app. PowerShell-native equivalent of program_board.sh.

.DESCRIPTION
  Picks the newest installed Vivado (handles both C:\Xilinx\<ver>\Vivado and
  C:\Xilinx\Vivado\<ver> layouts), pins vivado + xsdb/xsct to that version, and
  puts the tools\ no-op xlsclients shim on PATH so the Vitis launcher doesn't
  abort with "xlsclients not available". Run after every board power-cycle.

.EXAMPLE
  .\program_board.ps1
.EXAMPLE
  .\program_board.ps1 -NoEth          # FPGA + MicroBlaze only (UART features)
.EXAMPLE
  .\program_board.ps1 -Vivado 2024.1  # pin a version (default: newest)
.EXAMPLE
  .\program_board.ps1 -NoInit         # load A53 app without psu_init
#>
[CmdletBinding()]
param(
    [switch]$NoEth,
    [switch]$NoInit,
    [string]$Vivado
)
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

# tools\ holds the no-op xlsclients shim (.bat) for the Vitis X-probe.
$env:PATH = "$scriptDir\tools;$env:PATH"

function Get-XilinxVersions {
    $globs = @(
        "C:\Xilinx\*\Vivado\bin\vivado.bat",   # new layout: C:\Xilinx\<ver>\Vivado
        "C:\Xilinx\Vivado\*\bin\vivado.bat"     # old layout: C:\Xilinx\Vivado\<ver>
    )
    $vers = foreach ($g in $globs) {
        Get-ChildItem -Path $g -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_.FullName -match '(\d{4}\.\d+)') { $matches[1] }
        }
    }
    $vers | Sort-Object -Unique -Descending { [version]$_ }
}

function Resolve-Tool([string]$base, [string]$ver, [bool]$isVivado) {
    if ($isVivado) {
        $dirs = @("C:\Xilinx\$ver\Vivado\bin", "C:\Xilinx\Vivado\$ver\bin")
    } else {
        # xsdb ships under Vivado; xsct under Vitis -- search both layouts.
        $dirs = @("C:\Xilinx\$ver\Vivado\bin", "C:\Xilinx\Vivado\$ver\bin",
                  "C:\Xilinx\$ver\Vitis\bin",  "C:\Xilinx\Vitis\$ver\bin")
    }
    foreach ($d in $dirs) {
        foreach ($ext in ".bat", ".cmd") {
            $f = Join-Path $d "$base$ext"
            if (Test-Path $f) { return $f }
        }
    }
    return $null
}

$ver = if ($Vivado) { $Vivado } else { Get-XilinxVersions | Select-Object -First 1 }
if (-not $ver) {
    Write-Error "No Vivado found under C:\Xilinx. Pass -Vivado <ver> or check the install."
    exit 1
}
$found = (Get-XilinxVersions) -join ", "
Write-Host "Xilinx versions found: $found" -ForegroundColor DarkGray
Write-Host "Using version: $ver" -ForegroundColor Cyan

$vivadoExe = Resolve-Tool "vivado" $ver $true
$xsctExe   = Resolve-Tool "xsdb"   $ver $false      # prefer xsdb (headless)
if (-not $xsctExe) { $xsctExe = Resolve-Tool "xsct" $ver $false }

if (-not $vivadoExe) {
    Write-Error "vivado.bat not found for $ver. Pass -Vivado <ver> or set the path."
    exit 1
}

Write-Host "==> FPGA bitstream + MicroBlaze firmware   ($vivadoExe)" -ForegroundColor Green
& $vivadoExe -mode batch -source quiet.tcl -tclargs program_and_load.tcl
if ($LASTEXITCODE -ne 0) {
    Write-Error "Vivado step failed (exit $LASTEXITCODE). Not loading the A53 app."
    exit 1
}

if (-not $NoEth) {
    if (-not $xsctExe) {
        Write-Error "xsdb/xsct not found for $ver. Re-run with -NoEth, or pass -Vivado <ver>."
        exit 1
    }
    $ethArgs = @("quiet.tcl", "load_ps_eth_stream.tcl")
    if (-not $NoInit) { $ethArgs += "--init-ps" }
    Write-Host "==> A53 PS-Ethernet app $($ethArgs[2])   ($xsctExe)" -ForegroundColor Green
    & $xsctExe @ethArgs
}

Write-Host "==> Done. Launch the GUI: python scripts\dac_scope_qt.py --port COM10" -ForegroundColor Cyan
