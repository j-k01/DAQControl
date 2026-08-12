<#
.SYNOPSIS
  Bring the direct DAQ-board Ethernet connection online.

.DESCRIPTION
  Runs a bounded recovery ladder:
    1. Configure and test the host NIC.
    2. Restart the host NIC and clear stale neighbor state.
    3. Restart only the Linux DAQ Ethernet service over the PS UART.
    4. Reprogram the FPGA, MicroBlaze, and unified Linux/USB/DAQ runtime.
    5. Optionally wait for a physical power cycle and fully reprogram once more.

  Success requires an actual UDP PING/PONG exchange with the DAQ service.
  ICMP ping alone is not considered sufficient.

.EXAMPLE
  .\recover_ethernet.ps1

.EXAMPLE
  .\recover_ethernet.ps1 -InterfaceAlias "Ethernet 2"

.EXAMPLE
  .\recover_ethernet.ps1 -WaitForPowerCycle

.EXAMPLE
  .\recover_ethernet.ps1 -PlanOnly
#>
[CmdletBinding()]
param(
    [string]$InterfaceAlias = "Ethernet",
    [string]$LocalIp = "192.168.2.1",
    [string]$BoardIp = "192.168.2.10",
    [int]$CmdPort = 5006,
    [int]$ProbeAttempts = 5,
    [int]$ProbeTimeoutMs = 1500,
    [int]$LinkWaitSeconds = 20,
    [string]$PsPort = "COM9",
    [string]$Python,
    [string]$Remote,
    [string]$Identity,
    # Retained for command-line compatibility; the unified loader discovers
    # the remote tool version and does not pin a local Vivado/XSDB version.
    [string]$Vivado,
    [string]$Xsdb,
    [switch]$SkipAdapterRestart,
    [Alias("SkipProcessorRestart")]
    [switch]$SkipServiceRestart,
    [switch]$SkipFullProgram,
    [switch]$ForceFullProgram,
    [switch]$WaitForPowerCycle,
    [switch]$PlanOnly,
    [string]$LogPath
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:OriginalBoundParameters = @{} + $PSBoundParameters
Set-Location $root

function Test-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltinRole]::Administrator)
}

function Quote-ProcessArgument([string]$Value) {
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Restart-Elevated {
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Quote-ProcessArgument $PSCommandPath)
    )
    foreach ($entry in $script:OriginalBoundParameters.GetEnumerator()) {
        if ($entry.Value -is [System.Management.Automation.SwitchParameter]) {
            if ($entry.Value.IsPresent) { $arguments += "-$($entry.Key)" }
        } else {
            $arguments += "-$($entry.Key)"
            $arguments += (Quote-ProcessArgument ([string]$entry.Value))
        }
    }
    Write-Host "Requesting Administrator permission for NIC recovery..." -ForegroundColor Yellow
    $proc = Start-Process powershell.exe -Verb RunAs `
        -ArgumentList ($arguments -join " ") -Wait -PassThru
    exit $proc.ExitCode
}

function Resolve-BoardAdapter {
    param([string]$RequestedAlias)

    if ($RequestedAlias) {
        $selected = Get-NetAdapter -Name $RequestedAlias -ErrorAction SilentlyContinue
        if (-not $selected) {
            Write-Host "Adapter '$RequestedAlias' was not found. Physical adapters:" -ForegroundColor Red
            Get-NetAdapter -Physical | Select-Object Name,Status,LinkSpeed,InterfaceDescription |
                Format-Table -AutoSize
            throw "Unknown interface alias '$RequestedAlias'."
        }
        return $selected
    }

    $assigned = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $LocalIp `
        -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($assigned) {
        return Get-NetAdapter -InterfaceIndex $assigned.InterfaceIndex
    }

    $defaultInterfaces = @(
        Get-NetRoute -AddressFamily IPv4 -DestinationPrefix "0.0.0.0/0" `
            -ErrorAction SilentlyContinue | ForEach-Object { $_.InterfaceIndex }
    )
    $candidates = @(
        Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Status -eq "Up" -and
                $defaultInterfaces -notcontains $_.ifIndex
            }
    )
    if ($candidates.Count -eq 1) {
        return $candidates[0]
    }

    $nonDefaultAdapters = @(
        Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
            Where-Object { $defaultInterfaces -notcontains $_.ifIndex }
    )
    if ($nonDefaultAdapters.Count -eq 1) {
        Write-Warning ("Selecting the only physical adapter without a default " +
            "route, '$($nonDefaultAdapters[0].Name)', even though its link is " +
            "$($nonDefaultAdapters[0].Status).")
        return $nonDefaultAdapters[0]
    }

    Write-Host "Unable to select the board-link adapter safely." -ForegroundColor Red
    Get-NetAdapter -Physical | Select-Object Name,Status,LinkSpeed,InterfaceDescription |
        Format-Table -AutoSize
    if ($candidates.Count -gt 1) {
        $names = ($candidates | ForEach-Object { "'$($_.Name)'" }) -join ", "
        throw "Multiple Up adapters without a default route: $names. Pass -InterfaceAlias."
    }
    throw "No Up physical adapter without a default route. Check the cable or pass -InterfaceAlias."
}

function Invoke-ChildPowerShell {
    param(
        [Parameter(Mandatory=$true)][string]$Script,
        [string[]]$Arguments = @()
    )
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Script @Arguments |
        ForEach-Object { Write-Host $_ }
    $childExitCode = $LASTEXITCODE
    return [int]$childExitCode
}

function Resolve-ProjectPython {
    param([string]$RequestedPython)

    if ($RequestedPython) {
        return $RequestedPython
    }

    $venvPython = Join-Path $root ".venv\Scripts\python.exe"
    if (Test-Path $venvPython -PathType Leaf) {
        return $venvPython
    }

    throw ("The UV project environment is missing. From the repository root, " +
        "run 'uv sync --frozen', then rerun this recovery script.")
}

function Assert-RecoveryPython {
    & $Python -c "import serial"
    if ($LASTEXITCODE -ne 0) {
        throw ("The project Python environment cannot import pyserial. Run " +
            "'uv sync --frozen' from the repository root before recovery.")
    }
}

function Configure-HostPath {
    $arguments = @(
        "-InterfaceAlias", $script:Adapter.Name,
        "-LocalIp", $LocalIp,
        "-BoardIp", $BoardIp,
        "-SkipTest"
    )
    $exitCode = Invoke-ChildPowerShell `
        -Script (Join-Path $root "scripts\set_board_ethernet.ps1") `
        -Arguments $arguments
    if ($exitCode -ne 0) {
        throw "Host NIC configuration failed with exit code $exitCode."
    }
    $script:Adapter = Get-NetAdapter -Name $script:Adapter.Name
}

function Wait-BoardLink {
    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $LinkWaitSeconds))
    do {
        $script:Adapter = Get-NetAdapter -Name $script:Adapter.Name `
            -ErrorAction SilentlyContinue
        if ($script:Adapter -and $script:Adapter.Status -eq "Up") {
            Write-Host "Link is Up at $($script:Adapter.LinkSpeed)." -ForegroundColor Green
            return $true
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    Write-Warning "The '$($script:Adapter.Name)' link did not become Up."
    return $false
}

function Clear-BoardNeighbor {
    $neighbors = @(
        Get-NetNeighbor -InterfaceIndex $script:Adapter.ifIndex `
            -IPAddress $BoardIp -ErrorAction SilentlyContinue
    )
    foreach ($neighbor in $neighbors) {
        Remove-NetNeighbor -InputObject $neighbor -Confirm:$false `
            -ErrorAction SilentlyContinue
    }
}

function Restart-HostAdapter {
    Write-Host "Restarting only '$($script:Adapter.Name)'..." -ForegroundColor Cyan
    Disable-NetAdapter -Name $script:Adapter.Name -Confirm:$false
    Start-Sleep -Seconds 2
    Enable-NetAdapter -Name $script:Adapter.Name -Confirm:$false
    Wait-BoardLink | Out-Null
    Configure-HostPath
    Clear-BoardNeighbor
}

function Test-BoardUdp {
    param([string]$Stage)

    Write-Host "`nUDP application probe: $Stage" -ForegroundColor Cyan
    $localAddress = [System.Net.IPAddress]::Parse($LocalIp)
    $boardAddress = [System.Net.IPAddress]::Parse($BoardIp)
    $localEndPoint = New-Object System.Net.IPEndPoint($localAddress, 0)
    $payload = [System.Text.Encoding]::ASCII.GetBytes("PING")

    for ($attempt = 1; $attempt -le [Math]::Max(1, $ProbeAttempts); $attempt++) {
        $client = $null
        try {
            $client = New-Object System.Net.Sockets.UdpClient($localEndPoint)
            $client.Client.ReceiveTimeout = [Math]::Max(100, $ProbeTimeoutMs)
            $sent = $client.Send($payload, $payload.Length, $BoardIp, $CmdPort)
            $remote = New-Object System.Net.IPEndPoint(
                [System.Net.IPAddress]::Any, 0)
            $reply = $client.Receive([ref]$remote)
            $text = [System.Text.Encoding]::ASCII.GetString($reply).Trim()
            if ($text -eq "PONG" -and $remote.Address.Equals($boardAddress)) {
                Write-Host ("PASS: PONG from {0}:{1} on attempt {2}." -f `
                    $remote.Address, $remote.Port, $attempt) -ForegroundColor Green
                return $true
            }
            Write-Warning "Attempt $attempt returned '$text' from $remote."
        } catch [System.Net.Sockets.SocketException] {
            Write-Host "  attempt $attempt/${ProbeAttempts}: $($_.Exception.Message)"
        } finally {
            if ($client) { $client.Close() }
        }
        Start-Sleep -Milliseconds 300
    }
    Write-Warning "No valid PONG was received during '$Stage'."
    return $false
}

function Restart-LinuxDaqService {
    Write-Host "`nRestarting only the Linux DAQ Ethernet service on $PsPort..." `
        -ForegroundColor Yellow
    & $Python (Join-Path $root "scripts\recover_linux_daq_service.py") `
        "--port" $PsPort
    if ($LASTEXITCODE -ne 0) {
        Write-Warning ("The unified Linux shell/service was not recoverable over " +
            "$PsPort. The full unified loader is the next recovery stage.")
        return $false
    }
    Start-Sleep -Seconds 2
    return $true
}

function Program-CompleteBoard {
    Write-Host ("`nFully reprogramming FPGA, MicroBlaze, and unified " +
        "Linux/USB/DAQ runtime over JTAG...") `
        -ForegroundColor Yellow
    $arguments = @(
        (Join-Path $root "pico_usb\load_and_test.py"),
        "--port", $PsPort,
        "--board-ip", $BoardIp,
        "--local-ip", $LocalIp
    )
    if ($Remote) { $arguments += @("--remote", $Remote) }
    if ($Identity) { $arguments += @("--identity", $Identity) }
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Warning ("Full JTAG board programming failed with exit code " +
            "$LASTEXITCODE.")
        return $false
    }
    Configure-HostPath
    Clear-BoardNeighbor
    return $true
}

function Show-FinalDiagnostics {
    Write-Host "`nRunning final read-only diagnostics..." -ForegroundColor Cyan
    $arguments = @(
        "-BoardIp", $BoardIp,
        "-LocalIp", $LocalIp,
        "-InterfaceAlias", $script:Adapter.Name,
        "-CmdPort", "$CmdPort"
    )
    $arguments += @("-PsPort", $PsPort, "-Python", $Python)
    Invoke-ChildPowerShell `
        -Script (Join-Path $root "scripts\diagnose_board_ethernet.ps1") `
        -Arguments $arguments | Out-Null
}

if ($PlanOnly) {
    Write-Host "DAQ Ethernet recovery plan:" -ForegroundColor Cyan
    Write-Host "  1. Configure the selected direct-link NIC and firewall."
    Write-Host "  2. Require a valid UDP PONG from $BoardIp`:$CmdPort."
    Write-Host "  3. Clear ARP and restart only the selected NIC."
    Write-Host "  4. Restart only daq-eth-service through Linux on $PsPort."
    Write-Host "  5. Fully reprogram the FPGA, MicroBlaze, and unified Linux runtime."
    if ($WaitForPowerCycle) {
        Write-Host "  6. Wait for a physical power cycle, then fully reprogram again."
    }
    exit 0
}

if (-not (Test-Administrator)) {
    Restart-Elevated
}

if (-not $LogPath) {
    $logDirectory = Join-Path $env:TEMP "DAQControl"
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $LogPath = Join-Path $logDirectory (
        "ethernet_recovery_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
}

$transcriptStarted = $false
try {
    Start-Transcript -Path $LogPath -Force | Out-Null
    $transcriptStarted = $true
    Write-Host "DAQ Ethernet recovery" -ForegroundColor Cyan
    Write-Host "Log: $LogPath"

    $script:Adapter = Resolve-BoardAdapter -RequestedAlias $InterfaceAlias
    Write-Host "Board-link adapter: $($script:Adapter.Name)"
    Write-Host "Local/board: $LocalIp -> $BoardIp`:$CmdPort"
    Write-Host "Linux PS UART: $PsPort"
    $Python = Resolve-ProjectPython -RequestedPython $Python
    Assert-RecoveryPython
    Write-Host "Project Python: $Python"
    if ($Vivado -or $Xsdb) {
        Write-Warning ("-Vivado/-Xsdb are ignored by the unified recovery path; " +
            "the loader uses the available toolchain on its JTAG host.")
    }

    Configure-HostPath
    Wait-BoardLink | Out-Null
    Clear-BoardNeighbor

    if (-not $ForceFullProgram -and (Test-BoardUdp -Stage "initial host configuration")) {
        Write-Host "`nEthernet is online; no reset or programming was required." `
            -ForegroundColor Green
        exit 0
    }

    if (-not $ForceFullProgram -and -not $SkipAdapterRestart) {
        Restart-HostAdapter
        if (Test-BoardUdp -Stage "host adapter restart") {
            Write-Host "`nEthernet recovered after refreshing the host path." `
                -ForegroundColor Green
            exit 0
        }
    }

    if (-not $ForceFullProgram -and -not $SkipServiceRestart) {
        if (Restart-LinuxDaqService) {
            Clear-BoardNeighbor
            if (Test-BoardUdp -Stage "Linux DAQ service restart") {
                Write-Host "`nEthernet recovered after restarting daq-eth-service." `
                    -ForegroundColor Green
                exit 0
            }
        }
    }

    if (-not $SkipFullProgram) {
        if (Program-CompleteBoard) {
            if (-not $SkipAdapterRestart) {
                Restart-HostAdapter
            }
            if (Test-BoardUdp -Stage "complete board reprogramming") {
                Write-Host "`nEthernet recovered after complete board programming." `
                    -ForegroundColor Green
                exit 0
            }
        }
    }

    if ($WaitForPowerCycle) {
        Write-Host ("`nPower-cycle the board, wait for its power rails to settle, " +
            "then press Enter. The board will be fully reprogrammed next.") `
            -ForegroundColor Yellow
        Read-Host | Out-Null
        if (-not $SkipFullProgram) {
            if (Program-CompleteBoard) {
                if (-not $SkipAdapterRestart) {
                    Restart-HostAdapter
                }
                if (Test-BoardUdp -Stage "physical power cycle and reprogramming") {
                    Write-Host "`nEthernet recovered after the physical power cycle." `
                        -ForegroundColor Green
                    exit 0
                }
            }
        }
    }

    Show-FinalDiagnostics
    Write-Host "`nRECOVERY FAILED: no valid UDP PONG from $BoardIp." `
        -ForegroundColor Red
    Write-Host "Review the transcript: $LogPath"
    Write-Host "If the unified loader cannot reach the processors, power-cycle the board and rerun:"
    Write-Host "  .\recover_ethernet.ps1 -InterfaceAlias `"$($script:Adapter.Name)`" -WaitForPowerCycle"
    exit 3
} catch {
    Write-Host "`nRECOVERY ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Transcript: $LogPath"
    exit 2
} finally {
    if ($transcriptStarted) {
        try { Stop-Transcript | Out-Null } catch {}
    }
}
