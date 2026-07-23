# Rust continuous trigger-average architecture

The native application under `rust_gui/` separates acquisition from display
without creating competing board owners:

1. One worker thread exclusively owns the UART link and UDP burst socket.
2. `BCPT <size> <reps>` captures small batches of hardware-trigger-aligned
   repetitions.
3. `BRDO` drains the captured DDR data. An incomplete UDP drain is retried from
   the same DDR contents; it does not trigger another capture.
4. Each decoded repetition is inserted into a fixed-size ring. Four `i64`
   accumulator arrays are updated by adding the new repetition and subtracting
   the evicted oldest repetition.
5. The worker publishes one latest-only snapshot containing the current mean
   and capture statistics. There is no unbounded capture-to-GUI queue.
6. The egui thread samples that snapshot at up to 30 Hz and draws only a
   peak-preserving envelope of at most 4096 points per channel.

This preserves the FPGA's sample-zero trigger alignment. No software
cross-correlation or shifting is applied.

## Run

```powershell
cd rust_gui
cargo run --release
```

Or run the built executable directly:

```powershell
.\rust_gui\target\release\daq_scope.exe
```

In the Capture panel:

- Select the per-repetition capture size.
- Choose the rolling window, normally 8-16.
- Choose repetitions per batch, normally 4.
- Press **Start Live Trigger Average**.

The current player must already be configured and running because `BCPT`
waits for its hardware restart event. While continuous averaging owns the
capture path, other board commands are rejected until averaging is stopped.

## Correctness constraints

- UART and UDP have one owner.
- Early UDP packets received before the `BRDO` UART acknowledgement are kept if
  their observed request ID matches the acknowledgement.
- BCPT DDR stride is distinct from bytes per repetition; padding is never
  included in a repetition.
- Changing sample length resets the rolling window rather than mixing layouts.
- The viewer shows only the average. Rendering cannot block capture or build up
  stale frames.

Closing the native application stops its worker. If acquisition must survive
closing/restarting the viewer, this engine can later be moved into a service
and the same latest-snapshot structure exposed through shared memory.
