try {
    $devices = Get-CimInstance Win32_PnPEntity -ErrorAction Stop
} catch {
    Write-Warning "Could not query PnP device IDs: $($_.Exception.Message)"
    Write-Warning "Listing COM port names only. Use Device Manager -> Details -> Hardware Ids to find MI_02."
    [System.IO.Ports.SerialPort]::GetPortNames() |
        Sort-Object -Unique |
        ForEach-Object {
            [PSCustomObject]@{
                Port = $_
                Interface = "--"
                Role = "unknown; find the CP2108 device with MI_02 for DAQ_LAUNCH"
            }
        } |
        Format-Table -AutoSize
    return
}

$devices |
    Where-Object {
        $_.Name -match '\(COM[0-9]+\)' -and
        ($_.Name -match 'CP210|Silicon Labs|USB.*UART|USB Serial' -or
         $_.PNPDeviceID -match 'VID_10C4')
    } |
    Sort-Object PNPDeviceID |
    ForEach-Object {
        $com = if ($_.Name -match '\((COM[0-9]+)\)') { $Matches[1] } else { "COM?" }
        $mi = if ($_.PNPDeviceID -match 'MI_([0-9A-Fa-f]{2})') { $Matches[1].ToUpperInvariant() } else { "--" }
        $role = switch ($mi) {
            "00" { "CP2108 channel 0" }
            "01" { "CP2108 channel 1" }
            "02" { "CP2108 channel 2: ZCU102 PL UART, use this for DAQ_LAUNCH" }
            "03" { "CP2108 channel 3: MSP430 system controller, not DAQ_LAUNCH" }
            default { "unknown interface" }
        }

        [PSCustomObject]@{
            Port = $com
            Interface = if ($mi -ne "--") { "MI_$mi" } else { "--" }
            Role = $role
            Name = $_.Name
            DeviceId = $_.PNPDeviceID
        }
    } |
    Format-Table -AutoSize
