# DAQ scope + control — native Rust (egui) interface

A standalone, immediate-mode Rust rebuild of `scripts/dac_scope_qt.py`. Uses
[egui/eframe](https://github.com/emilk/egui) for instant, GPU-accelerated
redraws and `egui_plot` for the scope. No Python/Qt runtime — a single ~6 MB
native binary.

## Build / run

```
cd rust_gui
cargo run --release          # or: cargo build --release  ->  target/release/daq_scope.exe
```

Prereqs: a Rust toolchain (`rustup`), and the board's UART (default COM10) +
NIC at 192.168.2.1/24. Programming the FPGA is still done from the build host;
this app is the host-side control/scope, same as the Python GUI.

## Features (parity with the Python GUI)

- **4-channel scope** — Time or FFT (in-house radix-2 FFT), always-visible
  one-shot fit and optional continuous per-waveform Y autoscale; plots remain
  fully interactive (zoom/pan).
- **Visual crossbar (XBAR tab)** — the 16→4 routing drawn as lines; a route is
  **solid only once applied**, staged picks are **dashed**, so the picture never
  claims a switch the board hasn't taken. "Confirm route" commits via `NSRC`.
- **Neuron control** — built-in + saved **custom profiles**
  (`~/.daq_neuron_profiles.json`), per-neuron running-profile readout, live
  a/b/c/d/I params, and explicit integration `dt` + update-period timing.
  Connect restores the established `period=1`, `dt=0.5` operating point without
  changing any route or per-neuron profile.
- **Verified current player** — arbitrary, periodic, constant, and step
  programs are uploaded over UART, then registers 16 and 20 are read back to
  verify run mode, sample count, timing, and DAC-mirror gain.
- **DDS** tone (`DDSI`), **BRAM waveform** builder (`PROG`).
- **Legacy mod-4 baseline removal** — optional display-time diagnostic for old
  captures or small genuine core offsets; raw captures stay intact. Corrected
  FPGA images do not need it for the former +/-7 mV byte-pairing artifact.
- **Always-visible Capture bar** — UART Capture (`PCAP`), Collect Ethernet
  (`BCAP`/`BRDO` burst with auto-retry on dropped-packet drains), Auto-Sample.
- **Continuous trigger average** — a dedicated acquisition thread repeatedly
  runs hardware-aligned `BCPT` batches, maintains an incremental fixed-window
  average, and publishes only the newest result to the viewer. The plot is
  peak-preserving and capped at 4096 points/channel, so rendering never paces
  acquisition.
- Board controls remain available during continuous trigger averaging. They
  are serialized between completed BCPT batches, and a setting change starts a
  fresh rolling average so old and new configurations are never mixed.
- Explicit dark/light theme toggle; dark mode is the default.
- **Raw firmware command** box, STAT view, live-stream `STRM` start/stop.

## Layout

| file | role |
|------|------|
| `src/dsp.rs`   | pure DSP + constants (DDS/Q16 conv, de-interleave, waveforms, FFT) — unit tested |
| `src/burst_async.rs` | race-safe concurrent UDP reassembler + PCAP/chip decode |
| `src/rolling.rs` | fixed-memory add-new/subtract-old rolling accumulator |
| `src/proto.rs` | serial worker thread; command/event channels; board I/O off the UI thread |
| `src/app.rs`   | egui UI: scope, crossbar painter, control panels |
| `src/main.rs`  | eframe bootstrap |

`cargo test` covers the DSP, early-packet/request-ID race, strided BCPT layout,
live-mode command arbitration, rolling eviction arithmetic, and
spike-preserving display reduction.

## Not yet ported

- Continuous UDP live-stream plot (the `STRM` buttons issue the command;
  Auto-Sample's 1/s bursts cover the live-view need meanwhile).
- Pulse-shape editor pop-up.
- Captures save as `.csv` (counts) rather than `.npz`.
