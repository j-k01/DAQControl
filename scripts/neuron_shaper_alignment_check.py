#!/usr/bin/env python3
"""Hardware regression for neuron-driven pulse shaping and BCPT alignment."""

from __future__ import annotations

import argparse
import struct
import sys
import time

import numpy as np
import serial

from burst_capture import Reassembler, decode_chip, parse_brdo_request, uart_cmd


BOARD_IP = "192.168.2.10"
CMD_PORT = 5006
LOCAL_IP = "192.168.2.1"
LOCAL_PORT = 5005
ADC_FS_HZ = 1.0e9


def checked(ser, command, prefixes=("OK", "DAC xbar", "ERR"),
            timeout=10.0):
    reply = uart_cmd(ser, command, prefixes, timeout=timeout)
    if not reply or reply.startswith("ERR"):
        raise RuntimeError(f"{command} failed: {reply or '(no reply)'}")
    print(f"{command}: {reply}")
    return reply


def program_pulse(ser, neuron, samples):
    values = [max(-32768, min(32767, int(v))) for v in samples]
    ser.reset_input_buffer()
    ser.write(f"PULS ch {neuron} bin {len(values)}\n".encode("ascii"))
    ser.flush()
    ack = ""
    deadline = time.time() + 5.0
    while time.time() < deadline:
        line = ser.readline().decode("ascii", errors="replace").strip()
        if line.startswith(("PBRD", "ERR")):
            ack = line
            break
    if not ack.startswith("PBRD"):
        raise RuntimeError(f"PULS binary ready failed: {ack or '(no reply)'}")
    ser.write(struct.pack(f"<{len(values)}h", *values))
    ser.flush()
    reply = ""
    deadline = time.time() + 10.0
    while time.time() < deadline:
        line = ser.readline().decode("ascii", errors="replace").strip()
        if line.startswith(("PULS", "ERR")):
            reply = line
            break
    if not reply.startswith("PULS loaded"):
        raise RuntimeError(f"PULS upload failed: {reply or '(no reply)'}")
    print(reply)


def trapezoid_samples(peak=16000, pre=16, rise=24, high=80, fall=24, post=16):
    values = [0] * pre
    values += [int(round(peak * (i + 1) / rise)) for i in range(rise)]
    values += [peak] * high
    values += [int(round(peak * (fall - i - 1) / fall)) for i in range(fall)]
    values += [0] * post
    return values


def parse_bcpt(reply):
    meta = {}
    for token in reply.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        try:
            meta[key] = int(value, 0)
        except ValueError:
            pass
    required = ("reps", "bytes_per_rep", "stride", "total_per_chip")
    if not all(key in meta for key in required):
        raise RuntimeError(f"unparseable BCPT reply: {reply!r}")
    return meta


def triggered_capture(ser, kb, reps, retries=3):
    reply = uart_cmd(ser, f"BCPT {kb}k {reps}", ("OK BCPT", "ERR"), timeout=180.0)
    if not reply.startswith("OK BCPT"):
        raise RuntimeError(f"BCPT failed: {reply or '(no reply)'}")
    meta = parse_bcpt(reply)
    total = meta["total_per_chip"]
    asm = Reassembler(BOARD_IP, CMD_PORT, LOCAL_IP, LOCAL_PORT, total)
    try:
        for attempt in range(1 + max(0, retries)):
            if attempt:
                time.sleep(0.4)
            asm.begin_request()
            if not asm.register(timeout=2.0):
                raise RuntimeError("BRST registration timed out")
            brdo = uart_cmd(ser, "BRDO", ("OK BRDO", "ERR"), timeout=10.0)
            request = parse_brdo_request(brdo)
            if not brdo.startswith("OK BRDO") or request is None:
                raise RuntimeError(f"BRDO failed: {brdo or '(no request id)'}")
            asm.set_request_id(request)
            deadline = time.time() + max(10.0, 2.0 * total / 70.0e6 + 4.0)
            while time.time() < deadline and not asm.complete():
                if (asm.coverage(0) > 0 or asm.coverage(1) > 0) and asm.idle(0.8):
                    break
                time.sleep(0.05)
            if asm.complete():
                break
        if not asm.complete():
            raise RuntimeError(
                f"UDP incomplete after {retries + 1} drains: "
                f"chip0={100 * asm.coverage(0):.1f}% "
                f"chip1={100 * asm.coverage(1):.1f}%")
        channels = {}
        channels.update(decode_chip(asm.buf[0], 0))
        channels.update(decode_chip(asm.buf[1], 2))
    finally:
        asm.close()

    samples_per_rep = meta["bytes_per_rep"] // 4
    stride_samples = meta["stride"] // 4
    stack = {
        ch: np.stack([
            channels[ch][r * stride_samples:r * stride_samples + samples_per_rep]
            for r in range(meta["reps"])
        ]).astype(np.float64)
        for ch in range(4)
    }
    meta["coverage"] = 1.0
    meta["request"] = request
    meta["drain_attempts"] = attempt + 1
    return stack, meta


def alignment_offsets(stack, max_lag=64):
    candidates = []
    for ch, values in stack.items():
        median = np.median(values, axis=0)
        signal = float(np.std(median))
        residual = values - median
        noise = float(np.median(np.std(residual, axis=1)))
        candidates.append((signal / max(noise, 1.0), signal, ch, median))
    score, signal, anchor, reference = max(candidates)
    if signal < 4.0 or score < 1.5:
        return anchor, [0] * next(iter(stack.values())).shape[0], score
    reference = reference - reference.mean()
    offsets = []
    for row in stack[anchor]:
        row = row - row.mean()
        lags = range(-max_lag, max_lag + 1)
        scores = [np.dot(
            reference[max(0, lag):len(row) + min(0, lag)],
            row[max(0, -lag):len(row) - max(0, lag)]) for lag in lags]
        offsets.append(int(list(lags)[int(np.argmax(scores))]))
    return anchor, offsets, score


def structured_channel(stack, choices):
    scored = []
    for ch in choices:
        median = np.median(stack[ch], axis=0)
        residual = stack[ch] - median
        score = float(np.std(median)) / max(
            1.0, float(np.median(np.std(residual, axis=1))))
        scored.append((score, float(np.std(median)), ch))
    return max(scored)[2]


def pulse_metrics(stack, channel, baseline_stop=4000):
    average = np.mean(stack[channel], axis=0)
    stop = max(128, min(len(average) // 3, baseline_stop))
    baseline = float(np.median(average[128:stop]))
    delta = average - baseline
    pos = float(np.percentile(delta, 99.8))
    neg = float(np.percentile(delta, 0.2))
    excursion = pos if abs(pos) >= abs(neg) else neg
    threshold = max(8.0, 0.35 * abs(excursion))
    active = np.abs(delta) >= threshold
    starts = np.flatnonzero(active & ~np.r_[False, active[:-1]])
    return {
        "baseline": baseline,
        "excursion": excursion,
        "events": int(len(starts)),
        "average": average,
    }


def read_reg(ser, index):
    reply = uart_cmd(ser, f"RDRW {index}", (f"REG{index}", "ERR"), timeout=5.0)
    if not reply.startswith(f"REG{index}"):
        raise RuntimeError(f"RDRW {index} failed: {reply or '(no reply)'}")
    return int(reply.split("=", 1)[1].strip(), 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--profile", default="regular")
    parser.add_argument("--neuron", type=int, default=2, choices=range(4))
    parser.add_argument("--spike-dac-route", type=int, default=1, choices=range(4),
                        help="crossbar output feeding the physical DAC2 loopback")
    parser.add_argument("--kb", type=int, default=64)
    parser.add_argument("--reps", type=int, default=8)
    parser.add_argument("--frequency", type=float, default=100000.0)
    parser.add_argument("--current-ma", type=float, default=15.0)
    parser.add_argument("--constant-ma", type=float, default=12.0)
    parser.add_argument("--keep-config", action="store_true")
    args = parser.parse_args()

    period = max(2, min(1024, int(round(50.0e6 / args.frequency))))
    low_count = period // 2
    high_count = period - low_count
    amp_q16 = max(0, min(0x7FFFFFFF, int(round(args.current_ma * 65536.0))))
    const_q16 = max(0, min(0x7FFFFFFF, int(round(args.constant_ma * 65536.0))))
    cases = [
        ("unity", 0x4000, 0),
        ("half", 0x2000, 0),
        ("inverted", 0xC000, 0),
        ("offset_pos", 0x4000, 3000),
        ("offset_neg", 0x4000, -3000),
    ]
    captures = {}

    ser = serial.Serial(args.port, 115200, timeout=5, write_timeout=5)
    time.sleep(0.2)
    try:
        checked(ser, "STRM STOP", prefixes=("OK STRM", "ERR"))
        checked(ser, "WRTE 2 0x01000018")
        checked(ser, "COUP 1 dc")
        checked(ser, "COUP 3 dc")
        checked(ser, f"NEUR all {args.profile}")
        checked(ser, "NEUR all i 0")
        checked(ser, "NEUR all iconst 0")
        checked(ser, "NEUR all period 1")
        checked(ser, "NEUR all dt 0x00008000")

        before = [read_reg(ser, 25 + ch) for ch in range(4)]
        shape = trapezoid_samples()
        program_pulse(ser, args.neuron, shape)
        after = [read_reg(ser, 25 + ch) for ch in range(4)]
        expected_beats = (len(shape) + 3) // 4
        if after[args.neuron] != expected_beats:
            raise RuntimeError(
                f"neuron {args.neuron} length readback {after[args.neuron]} "
                f"!= {expected_beats}")
        for ch in range(4):
            if ch != args.neuron and after[ch] != before[ch]:
                raise RuntimeError(f"PULS ch {args.neuron} changed neuron {ch} length")

        checked(ser, "CURG 0x1400")
        checked(ser, f"CURS 1 {low_count} {high_count} 0x{amp_q16:08X} loop")
        checked(ser, "NSRC all off")
        checked(ser, "NSRC 0 current")
        checked(ser, f"NSRC {args.spike_dac_route} spike{args.neuron}")

        for name, gain, offset in cases:
            checked(ser, f"SCAL {args.neuron} 0x{gain:04X} {offset}")
            expected_cal = ((offset & 0xFFFF) << 16) | (gain & 0xFFFF)
            actual_cal = read_reg(ser, 21 + args.neuron)
            if actual_cal != expected_cal:
                raise RuntimeError(
                    f"{name}: calibration readback 0x{actual_cal:08X} != 0x{expected_cal:08X}")
            stack, meta = triggered_capture(ser, args.kb, args.reps)
            captures[name] = stack
            anchor, offsets, score = alignment_offsets(stack)
            print(f"{name}: anchor=ch{anchor} offsets={offsets} "
                  f"score={score:.1f} request={meta['request']} "
                  f"drains={meta['drain_attempts']}")
            if any(offset != 0 for offset in offsets):
                np.savez_compressed(
                    "captures/neuron_shaper_alignment_failure.npz",
                    **{f"{name}_ch{ch}": values
                       for ch, values in stack.items()})
                raise RuntimeError(f"{name}: nonzero raw trigger offsets {offsets}")

        raw_arrays = {}
        for case_name, case_stack in captures.items():
            for ch, values in case_stack.items():
                raw_arrays[f"{case_name}_ch{ch}"] = values
        np.savez_compressed("captures/neuron_shaper_alignment_last.npz",
                            **raw_arrays)
        current_ch = structured_channel(captures["unity"], (0, 1))
        spike_ch = structured_channel(captures["unity"], (2, 3))
        current_average = np.mean(captures["unity"][current_ch], axis=0)
        current_pp = float(np.percentile(current_average, 90) -
                           np.percentile(current_average, 10))
        period_samples = (low_count + high_count) * int(round(ADC_FS_HZ / 50.0e6))
        if len(current_average) <= period_samples:
            raise RuntimeError("capture is too short to compare current periods")
        first_period = current_average[:-period_samples]
        second_period = current_average[period_samples:]
        period_corr = float(np.corrcoef(first_period, second_period)[0, 1])
        period_nrms = float(np.std(first_period - second_period) /
                            max(np.std(first_period), 1.0))
        half_period_samples = period_samples // 2
        half_corr = float(np.corrcoef(
            current_average[:half_period_samples],
            current_average[half_period_samples:period_samples])[0, 1])
        if period_corr < 0.98 or period_nrms > 0.1:
            raise RuntimeError(
                f"current square does not repeat: corr={period_corr:.5f}, "
                f"nrms={period_nrms:.4f}")
        if half_corr > -0.5:
            raise RuntimeError(f"current half-cycles are not opposed: corr={half_corr:.4f}")
        if current_pp < 20.0:
            raise RuntimeError(f"current square not visible: p90-p10={current_pp:.1f}")

        metrics = {name: pulse_metrics(stack, spike_ch)
                   for name, stack in captures.items()}
        unity_average = metrics["unity"]["average"]
        for name, item in metrics.items():
            delta_rms = float(np.std(item["average"] - unity_average))
            print(f"{name} metrics: baseline={item['baseline']:.1f} "
                  f"excursion={item['excursion']:.1f} events={item['events']} "
                  f"delta_rms_vs_unity={delta_rms:.1f}")
        unity = metrics["unity"]["excursion"]
        half = metrics["half"]["excursion"]
        inverted = metrics["inverted"]["excursion"]
        if abs(unity) < 100.0:
            raise RuntimeError(f"unity neuron pulse too small: {unity:.1f} counts")
        ratio = abs(half / unity)
        if not 0.25 <= ratio <= 0.75:
            raise RuntimeError(f"half-gain ratio {ratio:.3f} outside 0.25..0.75")
        if np.sign(inverted) == np.sign(unity):
            raise RuntimeError(
                f"inversion failed: unity={unity:.1f}, inverted={inverted:.1f}")

        baseline_pos = metrics["offset_pos"]["baseline"]
        baseline_neg = metrics["offset_neg"]["baseline"]
        offset_span = abs(baseline_pos - baseline_neg)
        if offset_span < 20.0:
            print("WARN: DC offset is not observable through this analog loopback; "
                  "digital register/readback and RTL checks still apply")

        # Constant-drive sanity check: keep a zero-amplitude player running as
        # the BCPT timebase, and let iconst alone make the neuron spike.
        checked(ser, f"NEUR all {args.profile}")
        checked(ser, "NEUR all i 0")
        checked(ser, f"NEUR all iconst 0x{const_q16:08X}")
        checked(ser, f"CURS 1 {low_count} {high_count} 0x00000000 loop")
        checked(ser, f"SCAL {args.neuron} 0x4000 0")
        constant_stack, constant_meta = triggered_capture(ser, args.kb, args.reps)
        constant_metrics = pulse_metrics(constant_stack, spike_ch)
        if abs(constant_metrics["excursion"]) < 100.0:
            raise RuntimeError("constant current drive did not produce neuron pulses")

        print("\nPASS neuron-driven shaper/alignment regression")
        print(f"  physical current loopback transport ch{current_ch}: "
              f"square p90-p10={current_pp:.1f}, period_corr={period_corr:.6f}, "
              f"half_corr={half_corr:.4f}, period_nrms={period_nrms:.4f}")
        print(f"  physical spike loopback transport ch{spike_ch}: "
              f"unity={unity:.1f}, half={half:.1f} ({ratio:.3f}x), "
              f"inverted={inverted:.1f} counts")
        print(f"  offset baselines: +3000={baseline_pos:.1f}, "
              f"-3000={baseline_neg:.1f}, span={offset_span:.1f} ADC counts")
        print(f"  constant iconst={args.constant_ma:g} mA pulse excursion="
              f"{constant_metrics['excursion']:.1f}, "
              f"drains={constant_meta['drain_attempts']}")
        print(f"  pulse length={len(shape)} samples ({expected_beats} DAC beats); "
              "other neuron lengths unchanged")
    finally:
        if not args.keep_config:
            try:
                uart_cmd(ser, f"SCAL {args.neuron} default", ("OK SCAL", "ERR"), timeout=5.0)
                uart_cmd(ser, f"PULS ch {args.neuron} default", ("PULS", "ERR"), timeout=5.0)
                uart_cmd(ser, "NSRC all dds", ("DAC xbar", "ERR"), timeout=5.0)
                uart_cmd(ser, "COUP 1 ac", ("OK COUP", "ERR"), timeout=5.0)
                uart_cmd(ser, "COUP 3 ac", ("OK COUP", "ERR"), timeout=5.0)
            except Exception as exc:  # noqa: BLE001
                print(f"WARN cleanup failed: {exc}", file=sys.stderr)
        ser.close()


if __name__ == "__main__":
    raise SystemExit(main())
