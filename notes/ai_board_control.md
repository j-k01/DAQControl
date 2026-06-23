# AI Board Control Interface

This repo now has a small stable control surface for board tasks that agents
should call before writing one-off scripts.

## Human CLI

Run from the repo root:

```powershell
python scripts\daqctl.py ports
python scripts\daqctl.py status --json
python scripts\daqctl.py route --dac0 current --dac1 monitor0 --dac2 spike0 --json
python scripts\daqctl.py program --json

# tone / neuron / streaming / acquisition
python scripts\daqctl.py describe --json
python scripts\daqctl.py dds --freq 62.5e6
python scripts\daqctl.py neuron --target 0 --profile fast
python scripts\daqctl.py neuron --target 1 --param a=0.03 --param iconst=12
python scripts\daqctl.py stream --decim 256 --cic
python scripts\daqctl.py stop
python scripts\daqctl.py ping
python scripts\daqctl.py collect --kb 64 --json      # burst capture over Ethernet
python scripts\daqctl.py capture --frames 512 --json # ADC snapshot over UART
```

The default assumptions match the current setup:

- PL UART: `COM10`
- Programming host: `jkincaid@capitolpeak.ece.ucdavis.edu`
- Remote repo: `/home/jkincaid/DAQControl`
- Branch: `merge-stream-neuron`
- SSH key: `%USERPROFILE%\.ssh\capitolpeak_auto`

## MCP Server

Use this command from an MCP client configuration:

```json
{
  "mcpServers": {
    "daq-launch": {
      "command": "python",
      "args": [
        "D:\\DAVIS\\Research\\HighSpeedDAQ\\DAQ_LAUNCH\\scripts\\daq_mcp_server.py"
      ]
    }
  }
}
```

Available tools (server `daq-launch-control` v0.2.0):

Discovery / status
- `daq_describe` — static defaults + capabilities (sources, profiles, params), no I/O
- `daq_list_uart_ports`
- `daq_status`
- `daq_ping_board` — ICMP-ping the A53 ethernet IP (independent of UART)

Configuration (UART)
- `daq_uart_command` — raw line commands
- `daq_set_dac_routes` — 16:4 DAC crossbar (`NSRC`)
- `daq_set_dds` — DDS tone via `DDSI` (`freq_hz` or raw `inc`)
- `daq_program_neuron` — built-in profile and/or a/b/c/d/i/iconst/dt/period params
- `daq_start_stream` / `daq_stop_stream` / `daq_set_cic` — cyclic UDP stream control

Acquisition
- `daq_collect_ethernet` — one-shot full-rate burst capture (BCAP+BRDO+UDP), per-channel
  summary + coverage, saves a `.npz`; retries on incomplete UDP drain
- `daq_uart_capture` — 4-channel ADC snapshot over UART (PCAP), no Ethernet needed

Build / program
- `daq_program_board_via_capitolpeak`

Example tool arguments:

```json
// daq_set_dac_routes — validates names, sends NSRC, reads STAT, returns decoded routes
{ "routes": { "0": "dds", "1": "monitor0", "2": "spike0" } }

// daq_program_neuron — profile and/or params (a/b/c/d/i/iconst physical -> Q16.16)
{ "target": "1", "profile": "fast", "params": { "a": 0.03, "iconst": 12 } }

// daq_collect_ethernet — the reliable way to actually read the ADC
{ "kb": 64, "label": "probe" }
```

`daq_collect_ethernet` returns `{complete, attempts, coverage:{chip0,chip1},
channels:[{ch,samples,min,max,mean,rms_counts,vpp,dominant_freq_mhz}], saved}` —
a compact summary, not raw samples; the raw int16 channels go to the `.npz`.

## Direction

Add new board actions to `scripts/daq_control.py` first, then expose them in
both `scripts/daqctl.py` and `scripts/daq_mcp_server.py`.  That keeps the human
and AI interfaces using the same implementation.
