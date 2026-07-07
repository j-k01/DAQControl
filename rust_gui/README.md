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

- **4-channel scope** — Time or FFT (in-house radix-2 FFT), autoscale; plots are
  fully interactive (zoom/pan) — an upgrade over the fixed-span Qt view.
- **Visual crossbar (XBAR tab)** — the 16→4 routing drawn as lines; a route is
  **solid only once applied**, staged picks are **dashed**, so the picture never
  claims a switch the board hasn't taken. "Confirm route" commits via `NSRC`.
- **Neuron control** — built-in + saved **custom profiles**
  (`~/.daq_neuron_profiles.json`), per-neuron running-profile readout, live
  a/b/c/d/I params, sim-speed (`dt`).
- **DDS** tone (`DDSI`), **BRAM waveform** builder (`PROG`).
- **De-interleave baseline (mod-4)** — optional display-time removal of the
  ADS54J60 4-core interleave offset square; raw captures kept intact.
- **Always-visible Capture bar** — UART Capture (`PCAP`), Collect Ethernet
  (`BCAP`/`BRDO` burst with auto-retry on dropped-packet drains), Auto-Sample.
- **Raw firmware command** box, STAT view, live-stream `STRM` start/stop.

## Layout

| file | role |
|------|------|
| `src/dsp.rs`   | pure DSP + constants (DDS/Q16 conv, de-interleave, waveforms, FFT) — unit tested |
| `src/burst.rs` | UDP burst reassembler + PCAP/chip decode |
| `src/proto.rs` | serial worker thread; command/event channels; board I/O off the UI thread |
| `src/app.rs`   | egui UI: scope, crossbar painter, control panels |
| `src/main.rs`  | eframe bootstrap |

`cargo test` covers the DSP (DDS round-trip, Q16.16, de-interleave, waveform
framing).

## Not yet ported

- Continuous UDP live-stream plot (the `STRM` buttons issue the command;
  Auto-Sample's 1/s bursts cover the live-view need meanwhile).
- Current-source and pulse-shape editor pop-ups.
- Captures save as `.csv` (counts) rather than `.npz`.
