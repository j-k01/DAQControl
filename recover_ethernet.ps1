<#
.SYNOPSIS
  Bring the direct DAQ-board Ethernet connection online.

.DESCRIPTION
  Runs a bounded recovery ladder:
    1. Configure and test the host NIC.
    2. Restart the host NIC and clear stale neighbor state.
    3. Check and restart the A53 Ethernet application once.
    4. Reprogram the FPGA, MicroBlaze, and A53 application.
    5. Optionally wait for a physical power cycle and try once more.

  Success requires an actual UDP PING/PONG exchange with the A53 application.
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
    [string]$InterfaceAlias,
    [string]$LocalIp = "192.168.2.1",
    [string]$BoardIp = "192.168.2.10",
    [int]$CmdPort = 5006,
    [int]$ProbeAttempts = 5,
    [int]$ProbeTimeoutMs = 1500,
    [int]$LinkWaitSeconds = 20,
    [string]$Vivado,
    [string]$Xsdb,
    [switch]$SkipAdapterRestart,
    [switch]$SkipProcessorRestart,
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

function Resolve-Xsdb {
    if ($Xsdb) {
        if (-not (Test-Path $Xsdb)) { throw "XSDB not found: $Xsdb" }
        return (Resolve-Path $Xsdb).Path
    }
    if ($Vivado) {
        $versionCandidates = @(
            "C:\Xilinx\$Vivado\Vivado\bin\xsdb.bat"
            "C:\Xilinx\Vivado\$Vivado\bin\xsdb.bat"
            "C:\Xilinx\$Vivado\Vitis\bin\xsdb.bat"
            "C:\Xilinx\Vitis\$Vivado\bin\xsdb.bat"
        )
        $versionMatch = $versionCandidates |
            Where-Object { Test-Path $_ } |
            Select-Object -First 1
        if ($versionMatch) { return (Resolve-Path $versionMatch).Path }
        throw "XSDB was not found for requested Xilinx version $Vivado."
    }
    $candidates = @(
        Get-ChildItem "C:\Xilinx\*\Vivado\bin\xsdb.bat" -ErrorAction SilentlyContinue
        Get-ChildItem "C:\Xilinx\Vivado\*\bin\xsdb.bat" -ErrorAction SilentlyContinue
        Get-ChildItem "C:\Xilinx\*\Vitis\bin\xsdb.bat" -ErrorAction SilentlyContinue
        Get-ChildItem "C:\Xilinx\Vitis\*\bin\xsdb.bat" -ErrorAction SilentlyContinue
    ) | Sort-Object FullName -Descending
    if ($candidates.Count) { return $candidates[0].FullName }
    $command = Get-Command xsdb.bat,xsdb -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) { return $command.Source }
    return $null
}

function Invoke-ChildPowerShell {
    param(
        [Parameter(Mandatory=$true)][string]$Script,
        [string[]]$Arguments = @()
    )
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Script @Arguments
    return $LASTEXITCODE
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

function Test-A53Mailbox {
    if (-not $script:XsdbExe) {
        Write-Warning "XSDB is unavailable; the A53 heartbeat cannot be inspected."
        return $false
    }
    Write-Host "`nChecking the A53 mailbox/heartbeat..." -ForegroundColor Cyan
    & $script:XsdbExe (Join-Path $root "scripts\test_eth_mailbox.tcl")
    return ($LASTEXITCODE -eq 0)
}

function Restart-A53Application {
    if (-not $script:XsdbExe) {
        Write-Warning "XSDB is unavailable; skipping the controlled A53 restart."
        return $false
    }

    $psElf = (Resolve-Path (Join-Path $root "prebuilt\ps_eth_stream.elf")).Path
    $mbElf = (Resolve-Path (Join-Path $root "prebuilt\firmware.elf")).Path
    Write-Host "`nRestarting the A53 Ethernet app once without PS initialization..." `
        -ForegroundColor Yellow
    & $script:XsdbExe (Join-Path $root "load_ps_eth_stream.tcl") $psElf
    $a53Exit = $LASTEXITCODE

    Write-Host "Restoring MicroBlaze firmware without PS initialization..."
    & $script:XsdbExe (Join-Path $root "load_mb_firmware.tcl") $mbElf "--no-ps-init"
    $mbExit = $LASTEXITCODE
    if ($mbExit -ne 0) {
        throw "MicroBlaze restore failed after A53 restart (exit $mbExit)."
    }
    if ($a53Exit -ne 0) {
        Write-Warning "A53 restart failed with exit code $a53Exit."
        return $false
    }
    Start-Sleep -Seconds 8
    return $true
}

function Program-CompleteBoard {
    Write-Host "`nProgramming tracked FPGA, MicroBlaze, and A53 artifacts..." `
        -ForegroundColor Yellow
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $root "program_board.ps1"),
        "-NoPing", "-NoNicSetup",
        "-BoardIp", $BoardIp,
        "-HostIp", $LocalIp
    )
    if ($Vivado) { $arguments += @("-Vivado", $Vivado) }
    & powershell.exe @arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Complete board programming failed with exit code $LASTEXITCODE."
        return $false
    }
    Start-Sleep -Seconds 10
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
    if ($script:XsdbExe) { $arguments += @("-Xsdb", $script:XsdbExe) }
    Invoke-ChildPowerShell `
        -Script (Join-Path $root "scripts\diagnose_board_ethernet.ps1") `
        -Arguments $arguments | Out-Null
}

if ($PlanOnly) {
    Write-Host "DAQ Ethernet recovery plan:" -ForegroundColor Cyan
    Write-Host "  1. Configure the selected direct-link NIC and firewall."
    Write-Host "  2. Require a valid UDP PONG from $BoardIp`:$CmdPort."
    Write-Host "  3. Clear ARP and restart only the selected NIC."
    Write-Host "  4. Inspect A53 heartbeat and perform one no-init A53 reload."
    Write-Host "  5. Program tracked FPGA, MicroBlaze, and A53 artifacts."
    if ($WaitForPowerCycle) {
        Write-Host "  6. Wait for a physical power cycle and make one final attempt."
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
    $script:XsdbExe = Resolve-Xsdb
    Write-Host "Board-link adapter: $($script:Adapter.Name)"
    Write-Host "Local/board: $LocalIp -> $BoardIp`:$CmdPort"
    if ($script:XsdbExe) {
        Write-Host "XSDB: $script:XsdbExe"
    } else {
        Write-Warning "XSDB was not found. Processor recovery will require program_board.ps1."
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

    if (-not $ForceFullProgram -and -not $SkipProcessorRestart) {
        Test-A53Mailbox | Out-Null
        if (Restart-A53Application) {
            Clear-BoardNeighbor
            if (Test-BoardUdp -Stage "controlled A53 restart") {
                Write-Host "`nEthernet recovered after restarting the A53 application." `
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
        Write-Host "`nPower-cycle the board, wait for its power rails to settle, then press Enter." `
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
    Write-Host "If JTAG reported EDITR timeout, physically power-cycle the board and rerun:"
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
