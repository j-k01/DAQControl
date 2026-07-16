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
    [switch]$NoPing,                  # skip NIC setup + reachability check
    [switch]$NoNicSetup,              # never touch the host NIC config
    [string]$BoardIp = "192.168.2.10",
    [string]$HostIp = "192.168.2.1",  # what the board expects this PC to be
    [int]$MaxEthRetries = 4,
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

function Ensure-BoardNic {
    # The A53 app, GUI, and UDP drain all assume this PC is $HostIp on the
    # direct board link. A freshly moved board lands on a machine whose NIC is
    # still on DHCP (169.254.x.x) -- configure it when it's unambiguous which
    # port is the board link (Up, physical, and NOT the internet uplink).
    param([string]$Ip)
    $have = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -like ($Ip -replace '\.\d+$', '.*') } |
        Select-Object -First 1
    if ($have) {
        Write-Host "Board-subnet NIC: $($have.InterfaceAlias) ($($have.IPAddress))"
        return $true
    }
    $gw = @((Get-NetRoute -DestinationPrefix '0.0.0.0/0' `
             -ErrorAction SilentlyContinue).InterfaceAlias)
    $cand = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
        Where-Object { $_.Status -eq 'Up' -and ($gw -notcontains $_.InterfaceAlias) })
    if ($cand.Count -eq 0) {
        Write-Warning ("No Up NIC without a default route found. Cable the board " +
            "link, then set: New-NetIPAddress -InterfaceAlias '<name>' " +
            "-IPAddress $Ip -PrefixLength 24")
        return $false
    }
    if ($cand.Count -gt 1) {
        $names = ($cand | ForEach-Object { $_.InterfaceAlias }) -join ', '
        Write-Warning ("Multiple candidate NICs ($names) -- not guessing. Set the " +
            "board-link one manually: New-NetIPAddress -InterfaceAlias '<name>' " +
            "-IPAddress $Ip -PrefixLength 24")
        return $false
    }
    $alias = $cand[0].InterfaceAlias
    Write-Host "Configuring '$alias' as $Ip/24 for the board link" -ForegroundColor Cyan
    try {
        New-NetIPAddress -InterfaceAlias $alias -IPAddress $Ip -PrefixLength 24 `
            -ErrorAction Stop | Out-Null
        return $true
    } catch {
        Write-Warning ("Could not set the IP (needs an elevated PowerShell?): $_" +
            "`nSet it manually: New-NetIPAddress -InterfaceAlias '$alias' " +
            "-IPAddress $Ip -PrefixLength 24")
        return $false
    }
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

$trackedBit = Join-Path $scriptDir "prebuilt\top.bit"
$trackedElf = Join-Path $scriptDir "prebuilt\firmware.elf"
$trackedPsElf = Join-Path $scriptDir "prebuilt\ps_eth_stream.elf"
Write-Host "==> FPGA bitstream + MicroBlaze firmware   ($vivadoExe)" -ForegroundColor Green
Write-Host "    bit: $trackedBit" -ForegroundColor DarkGray
Write-Host "    elf: $trackedElf" -ForegroundColor DarkGray
# Pass both tracked artifacts explicitly. program_and_load.tcl's developer
# default prefers a local Vitis workspace ELF when one exists; that is useful
# during builds but unsafe on a git-pull target because the ignored workspace
# may be stale and incompatible with the newly pulled bitstream.
& $vivadoExe -mode batch -source quiet.tcl -tclargs program_and_load.tcl `
    $trackedBit $trackedElf
if ($LASTEXITCODE -ne 0) {
    Write-Error "Vivado step failed (exit $LASTEXITCODE). Not loading the A53 app."
    exit 1
}

if (-not $NoEth) {
    if (-not $xsctExe) {
        # No PS command ran, so the MicroBlaze loaded above is still intact.
        Write-Warning ("xsdb/xsct not found for $ver; Ethernet skipped. " +
            "UART/XBar control remains available.")
    } else {
        $ethFailed = $false
        # Run xsdb/xsct directly (NOT via quiet.tcl). Ethernet is optional: an
        # A53/GEM/PHY failure must never abort before UART firmware is restored.
        # Deployment must use the tracked PS ELF. An ignored local Vitis
        # workspace may contain an older app/platform and must not override it.
        $ethArgs = @("load_ps_eth_stream.tcl", $trackedPsElf)
        if (-not $NoInit) { $ethArgs += "--init-ps" }
        Write-Host "==> A53 PS-Ethernet app $($ethArgs -join ' ')   ($xsctExe)" -ForegroundColor Green
        & $xsctExe @ethArgs
        if ($LASTEXITCODE -ne 0) {
            $ethFailed = $true
            Write-Warning "Initial Ethernet load failed (exit $LASTEXITCODE); continuing with UART."
        }

        # psu_init/PS reset may disturb the fabric-side MicroBlaze even when
        # Ethernet subsequently fails. Restore UART immediately; later A53
        # retries omit PS init and therefore cannot reset it again.
        Write-Host "==> Restoring MicroBlaze UART firmware after PS initialization" -ForegroundColor Green
        & $xsctExe "load_mb_firmware.tcl" $trackedElf "--no-ps-init"
        if ($LASTEXITCODE -ne 0) {
            Write-Error "MicroBlaze UART restore failed (exit $LASTEXITCODE)."
            exit 1
        }

        if (-not $NoPing -and -not $ethFailed) {
            if (-not $NoNicSetup) { Ensure-BoardNic $HostIp | Out-Null }

            # Test the FIRST A53 instance before touching it. The old sequence
            # stopped/reset a potentially working app for an unconditional
            # second download; if that download hit EDITR timeout, Ethernet was
            # left dead. A reload is now allowed only when the first app has
            # neither ping nor an ARP presence.
            $up = $false
            Write-Host "==> Waiting for initial A53 PHY/autonegotiation" -ForegroundColor Green
            Start-Sleep -Seconds 5
            if (Test-Connection -ComputerName $BoardIp -Count 3 -Quiet) {
                $up = $true
            } else {
                $seen = (arp -a) | Select-String ([regex]::Escape($BoardIp))
                if ($seen) {
                    $ethFailed = $true
                    Write-Warning ("$BoardIp is present in ARP but not pinging. " +
                        "Preserving the running A53; check host firewall/VPN and run " +
                        "scripts\diagnose_board_ethernet.ps1.")
                } else {
                    for ($try = 1; $try -le $MaxEthRetries; $try++) {
                        Write-Host ("==> No ping/ARP from initial A53; controlled " +
                            "reload $try/$MaxEthRetries") -ForegroundColor Yellow
                        & $xsctExe "load_ps_eth_stream.tcl" $trackedPsElf
                        if ($LASTEXITCODE -ne 0) {
                            $ethFailed = $true
                            Write-Warning "Ethernet reload failed (exit $LASTEXITCODE); UART remains ready."
                            break
                        }
                        Start-Sleep -Seconds 10
                        if (Test-Connection -ComputerName $BoardIp -Count 3 -Quiet) {
                            $up = $true
                            break
                        }
                    }
                }
            }
            if ($up) {
                Write-Host "==> Board answering on $BoardIp" -ForegroundColor Green
            } elseif (-not $ethFailed) {
                $ethFailed = $true
                Write-Warning "Still no ping after Ethernet retries. A53 mailbox:"
                & $xsctExe "read_eth_mailbox.tcl"
                Write-Warning ("Ethernet is unavailable, but programming will " +
                    "continue and restore UART/XBar control.")
            }
        } elseif ($NoPing -and -not $ethFailed) {
            Write-Host ("==> Ethernet load completed without a destructive reload; " +
                "reachability skipped by -NoPing") -ForegroundColor Yellow
        }

        if ($ethFailed) {
            Write-Warning "Ethernet unavailable; UART capture and XBar control are ready."
        }
    }
}

Write-Host "==> UART/XBar control ready. Launch: python scripts\dac_scope_qt.py --port COM10" -ForegroundColor Cyan
