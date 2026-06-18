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

Available tools:

- `daq_list_uart_ports`
- `daq_status`
- `daq_uart_command`
- `daq_set_dac_routes`
- `daq_program_board_via_capitolpeak`

Example tool arguments:

```json
{
  "routes": {
    "0": "current",
    "1": "monitor0",
    "2": "spike0"
  }
}
```

The route tool validates source names, sends firmware `NSRC` commands, then
reads `STAT` and returns parsed `dac_xbar` plus decoded routes.

## Direction

Add new board actions to `scripts/daq_control.py` first, then expose them in
both `scripts/daqctl.py` and `scripts/daq_mcp_server.py`.  That keeps the human
and AI interfaces using the same implementation.
