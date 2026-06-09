import argparse
import csv
import math
import re
import struct
import sys
import time
from pathlib import Path

try:
    import serial
except ImportError as exc:
    raise SystemExit("pyserial is required: python -m pip install pyserial") from exc


SYNC_WORD = b"\xFE\x10\xCA\xFE"
DEFAULT_FRAMES = 4096
ADC_WORDS_PER_FRAME = 8
MAX_CAPTURE_FRAMES = 4096
MAX_PROGRAM_WORDS = 8192


def signed16(value):
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def clamp_u16(value):
    return max(0, min(0xFFFF, int(round(value))))


def clamp_s16(value):
    return max(-0x8000, min(0x7FFF, int(round(value))))


def encode_sample(value, sample_format):
    if sample_format == "twos":
        return clamp_s16(value) & 0xFFFF
    return clamp_u16(value)


def pack_pair(sample0, sample1, sample_format):
    return (
        (encode_sample(sample1, sample_format) << 16) |
        encode_sample(sample0, sample_format)
    )


def pack_raw_pair(sample0, sample1):
    return ((sample1 & 0xFFFF) << 16) | (sample0 & 0xFFFF)


def parse_chunk_order(text):
    tokens = [token for token in re.split(r"[\s,]+", text.strip()) if token]
    order = [int(token, 0) for token in tokens]
    if len(order) != 4 or sorted(order) != [0, 1, 2, 3]:
        raise argparse.ArgumentTypeError("--chunk-order must be a permutation of 0,1,2,3")
    return order


def reorder_u32_program_chunks(words, order):
    if order == [0, 1, 2, 3]:
        return words
    if len(words) % 2:
        raise ValueError("DAC 64-bit program frames require an even number of u32 words")

    out = []
    for index in range(0, len(words), 2):
        low_word = words[index]
        high_word = words[index + 1]
        chunks = [
            low_word & 0xFFFF,
            (low_word >> 16) & 0xFFFF,
            high_word & 0xFFFF,
            (high_word >> 16) & 0xFFFF,
        ]
        reordered = [chunks[slot] for slot in order]
        out.append(reordered[0] | (reordered[1] << 16))
        out.append(reordered[2] | (reordered[3] << 16))
    return out


def parse_u32_token(token):
    token = token.strip()
    if not token:
        raise ValueError("empty token")
    return int(token, 0) & 0xFFFFFFFF


def load_text_program(path):
    words = []
    numeric = re.compile(r"^(?:0x[0-9a-fA-F]+|[0-9]+)$")
    with path.open(newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            if row[0].strip().startswith("#"):
                continue
            cells = [cell.strip() for cell in row]
            if len(cells) > 1 and numeric.match(cells[1]):
                words.append(parse_u32_token(cells[1]))
                continue
            for cell in cells:
                if numeric.match(cell):
                    words.append(parse_u32_token(cell))
                    break
    return words


def load_program(path):
    path = Path(path)
    if path.suffix.lower() == ".bin":
        data = path.read_bytes()
        if len(data) % 4:
            raise ValueError(f"{path} length is not a multiple of 4 bytes")
        return list(struct.unpack(f"<{len(data) // 4}I", data))
    return load_text_program(path)


def triangle_value(index, frequency_hz, sample_rate_hz, amplitude, offset):
    phase = (index * frequency_hz / sample_rate_hz) % 1.0
    if phase < 0.5:
        return offset - amplitude + (4.0 * amplitude * phase)
    return offset + amplitude - (4.0 * amplitude * (phase - 0.5))


def make_triangle_program(
    words,
    step,
    offset,
    amplitude,
    sample_format,
    frequency_hz=None,
    sample_rate_hz=None,
):
    if frequency_hz is not None:
        out = []
        for index in range(words):
            out.append(pack_pair(
                triangle_value(2 * index, frequency_hz, sample_rate_hz, amplitude, offset),
                triangle_value(2 * index + 1, frequency_hz, sample_rate_hz, amplitude, offset),
                sample_format,
            ))
        return out

    if sample_format == "twos":
        low = clamp_s16(offset - amplitude)
        high = clamp_s16(offset + amplitude)
    else:
        low = clamp_u16(offset - amplitude)
        high = clamp_u16(offset + amplitude)
    sample = low
    rising = True
    out = []

    for _ in range(words):
        pair = []
        for _ in range(2):
            pair.append(sample)
            if rising:
                sample = min(high, sample + step)
                if sample >= high:
                    rising = False
            else:
                sample = max(low, sample - step)
                if sample <= low:
                    rising = True
        out.append(pack_pair(pair[0], pair[1], sample_format))
    return out


def make_sine_program(words, cycles, amplitude, offset, sample_format, phase=0.0):
    out = []
    sample_count = max(1, words * 2)
    for index in range(words):
        phase0 = 2.0 * math.pi * cycles * (2 * index) / sample_count + phase
        phase1 = 2.0 * math.pi * cycles * (2 * index + 1) / sample_count + phase
        out.append(pack_pair(
            offset + amplitude * math.sin(phase0),
            offset + amplitude * math.sin(phase1),
            sample_format,
        ))
    return out


def make_square_sine_program(words, square_period_words, sine_cycles, amplitude, offset, sample_format):
    half = words // 2
    high = offset + amplitude
    low = offset - amplitude
    out = []

    for index in range(half):
        phase = (index // max(1, square_period_words // 2)) & 1
        sample = high if phase == 0 else low
        out.append(pack_pair(sample, sample, sample_format))

    sine_word_count = words - half
    sine_sample_count = max(1, sine_word_count * 2)
    for index in range(sine_word_count):
        phase0 = 2.0 * math.pi * sine_cycles * (2 * index) / sine_sample_count
        phase1 = 2.0 * math.pi * sine_cycles * (2 * index + 1) / sine_sample_count
        out.append(pack_pair(
            offset + amplitude * math.sin(phase0),
            offset + amplitude * math.sin(phase1),
            sample_format,
        ))
    return out


def trapezoid_value(index, period_samples, amplitude, offset):
    segment = max(1, period_samples // 4)
    top_start = segment
    fall_start = top_start + segment
    bottom_start = fall_start + segment
    phase = index % period_samples
    low = offset - amplitude
    high = offset + amplitude

    if phase < top_start:
        return low + (high - low) * phase / max(1, top_start - 1)
    if phase < fall_start:
        return high
    if phase < bottom_start:
        fall_phase = phase - fall_start
        return high - (high - low) * fall_phase / max(1, bottom_start - fall_start - 1)
    return low


def make_trapezoid_program(words, frequency_hz, sample_rate_hz, amplitude, offset, sample_format):
    period_samples = int(round(sample_rate_hz / frequency_hz))
    period_samples = max(64, ((period_samples + 3) // 4) * 4)
    out = []
    for index in range(words):
        out.append(pack_pair(
            trapezoid_value(2 * index, period_samples, amplitude, offset),
            trapezoid_value(2 * index + 1, period_samples, amplitude, offset),
            sample_format,
        ))
    return out


def make_byte_pattern_program(words, high_start, high_step, low_start, low_step):
    out = []
    for index in range(words):
        sample_index0 = 2 * index
        sample_index1 = sample_index0 + 1
        high0 = (high_start + high_step * sample_index0) & 0xFF
        low0 = (low_start + low_step * sample_index0) & 0xFF
        high1 = (high_start + high_step * sample_index1) & 0xFF
        low1 = (low_start + low_step * sample_index1) & 0xFF
        out.append(pack_raw_pair((high0 << 8) | low0, (high1 << 8) | low1))
    return out


def make_program(args):
    if args.program:
        program = load_program(args.program)
    elif args.program_mode == "sine":
        program = make_sine_program(
            args.program_words,
            args.sine_cycles,
            args.amplitude,
            args.offset,
            args.sample_format,
        )
    elif args.program_mode == "triangle":
        triangle_frequency_hz = None
        sample_rate_hz = None
        if args.triangle_frequency_mhz is not None:
            triangle_frequency_hz = args.triangle_frequency_mhz * 1.0e6
            sample_rate_hz = args.sample_rate_mhz * 1.0e6
        program = make_triangle_program(
            args.program_words,
            args.triangle_step,
            args.offset,
            args.amplitude,
            args.sample_format,
            triangle_frequency_hz,
            sample_rate_hz,
        )
    elif args.program_mode == "square-sine":
        program = make_square_sine_program(
            args.program_words,
            args.square_period_words,
            args.sine_cycles,
            args.amplitude,
            args.offset,
            args.sample_format,
        )
    elif args.program_mode == "trapezoid":
        program = make_trapezoid_program(
            args.program_words,
            args.trapezoid_frequency_mhz * 1.0e6,
            args.sample_rate_mhz * 1.0e6,
            args.amplitude,
            args.offset,
            args.sample_format,
        )
    elif args.program_mode == "byte-pattern":
        program = make_byte_pattern_program(
            args.program_words,
            args.byte_high_start,
            args.byte_high_step,
            args.byte_low_start,
            args.byte_low_step,
        )
    else:
        program = []

    if len(program) > MAX_PROGRAM_WORDS:
        raise ValueError(f"program has {len(program)} words; max is {MAX_PROGRAM_WORDS}")
    if program and len(program) % 2:
        raise ValueError("DAC 64-bit program frames require an even number of u32 words")
    return reorder_u32_program_chunks(program, args.chunk_order)


def read_line(port):
    line = bytearray()
    while True:
        byte = port.read(1)
        if not byte:
            raise TimeoutError("timed out waiting for UART line")
        line.extend(byte)
        if byte == b"\n":
            return bytes(line).decode("ascii", errors="replace").strip()


def wait_for_line_prefix(port, prefix):
    while True:
        line = read_line(port)
        if line.startswith(prefix):
            return line


def parse_reg_line(line, register_name):
    pattern = re.compile(rf"^{register_name}\s*=\s*0x([0-9a-fA-F]{{8}})\s*$")
    match = pattern.match(line.strip())
    if not match:
        raise TimeoutError(f"unexpected {register_name} line: {line!r}")
    return int(match.group(1), 16)


def uart_command_ok(port, command):
    port.write((command + "\n").encode("ascii"))
    port.flush()
    print(wait_for_line_prefix(port, "OK"))


def read_rw_register(port, index):
    port.write(f"RDRW {index}\n".encode("ascii"))
    port.flush()
    return parse_reg_line(wait_for_line_prefix(port, f"RW{index}"), f"RW{index}")


def read_ro_register(port, index):
    port.write(f"RDRO {index}\n".encode("ascii"))
    port.flush()
    return parse_reg_line(wait_for_line_prefix(port, f"RO{index}"), f"RO{index}")


def check_build_id(port, expected):
    old_rw1 = read_rw_register(port, 1)
    uart_command_ok(port, "WRTE 1 3")
    actual = read_ro_register(port, 3)
    uart_command_ok(port, f"WRTE 1 0x{old_rw1:08X}")
    if actual != expected:
        raise ValueError(f"build ID mismatch: expected 0x{expected:08X}, got 0x{actual:08X}")
    print(f"Build ID OK: 0x{actual:08X}")


def set_rw2(port, value):
    uart_command_ok(port, f"WRTE 2 0x{value:08X}")
    actual = read_rw_register(port, 2)
    if actual != value:
        raise ValueError(f"RW2 write did not stick: expected 0x{value:08X}, got 0x{actual:08X}")
    print(f"RW2 set to 0x{actual:08X}")


def upload_program(port, words, channel):
    port.write(f"PROG {channel} {len(words)}\n".encode("ascii"))
    port.flush()
    print(wait_for_line_prefix(port, "PGRD"))
    port.write(struct.pack(f"<{len(words)}I", *words))
    port.flush()
    print(wait_for_line_prefix(port, "OK PROG"))
    frame_count = len(words) // 2
    rw3_value = (frame_count << 8) & 0xFFFFFF00
    port.write(f"WRTE 3 0x{rw3_value:08X}\n".encode("ascii"))
    port.flush()
    print(wait_for_line_prefix(port, "OK"))
    print(f"Set DAC BRAM loop frame_count={frame_count} via RW3=0x{rw3_value:08X}")


def read_dprd_words(port, channel, start, count):
    port.write(f"DPRD {channel} {start} {count}\n".encode("ascii"))
    port.flush()
    header = wait_for_line_prefix(port, "DPRD")
    print(header)
    if not header.startswith("DPRD ch="):
        print(wait_for_line_prefix(port, "DPRD ch="))
    words = []
    pattern = re.compile(r"^\s*(\d+):\s+0x([0-9a-fA-F]{8})\s*$")
    while len(words) < count:
        line = read_line(port)
        match = pattern.match(line)
        if not match:
            raise TimeoutError(f"unexpected DPRD line: {line!r}")
        words.append(int(match.group(2), 16))
    return words


def verify_program_upload(port, words, channel, verify_words):
    if verify_words <= 0 or not words:
        return

    first_count = min(verify_words, len(words))
    first_readback = read_dprd_words(port, channel, 0, first_count)
    if first_readback != words[:first_count]:
        raise ValueError(
            f"DAC channel {channel} first-word readback mismatch: "
            f"expected 0x{words[0]:08X}, got 0x{first_readback[0]:08X}"
        )

    if len(words) > first_count:
        last_start = len(words) - first_count
        last_readback = read_dprd_words(port, channel, last_start, first_count)
        if last_readback != words[last_start:]:
            raise ValueError(
                f"DAC channel {channel} last-word readback mismatch: "
                f"expected 0x{words[last_start]:08X}, got 0x{last_readback[0]:08X}"
            )

    print(f"Verified DAC channel {channel} BRAM readback ({first_count} first/last words).")


def parse_program_channels(text):
    text = text.strip().lower()
    if text == "all":
        return [0, 1, 2, 3]
    channels = []
    for token in text.replace(",", " ").split():
        channel = int(token, 0)
        if channel < 0 or channel > 3:
            raise ValueError("program channels must be 0..3 or 'all'")
        channels.append(channel)
    if not channels:
        raise ValueError("at least one program channel is required")
    return channels


def parse_sources(text):
    sources = []
    for token in text.replace(",", " ").split():
        source = int(token, 0)
        if source < 0 or source >= ADC_WORDS_PER_FRAME:
            raise ValueError(f"sources must be in the range 0..{ADC_WORDS_PER_FRAME - 1}")
        sources.append(source)
    if not sources:
        raise ValueError("at least one source is required")
    return sources


def parse_adc_converters(text):
    converters = []
    for token in text.replace(",", " ").split():
        converter = int(token, 0)
        if converter < 0 or converter > 3:
            raise ValueError("ADC channels must be 0..3 or a comma list")
        converters.append(converter)
    if not converters:
        raise ValueError("at least one ADC channel is required")
    return converters


def read_exact(port, count):
    data = bytearray()
    while len(data) < count:
        chunk = port.read(min(65536, count - len(data)))
        if not chunk:
            raise TimeoutError(f"expected {count} bytes, got {len(data)}")
        data.extend(chunk)
        print(
            f"\r{len(data)}/{count} bytes ({100 * len(data) // count}%)",
            end="",
            flush=True,
        )
    print()
    return bytes(data)


def wait_for_sync(port):
    window = bytearray()
    presync = bytearray()

    while True:
        byte = port.read(1)
        if not byte:
            tail = presync[-200:].decode("ascii", errors="replace")
            raise TimeoutError(f"timed out waiting for capture sync; UART tail={tail!r}")
        presync.extend(byte)
        window.extend(byte)
        if len(window) > len(SYNC_WORD):
            del window[0]
        if bytes(window) == SYNC_WORD:
            return bytes(presync[:-len(SYNC_WORD)])


def capture_frames(port, command_name, frames):
    port.reset_input_buffer()
    port.write(f"{command_name} {frames}\n".encode("ascii"))
    port.flush()
    presync = wait_for_sync(port)
    word_count = frames * ADC_WORDS_PER_FRAME
    raw = read_exact(port, word_count * 4)
    unpacked = struct.unpack(f"<{word_count}I", raw)
    return presync, list(unpacked)


def split_frame_captures(frame_words):
    captures = {source: [] for source in range(ADC_WORDS_PER_FRAME)}
    if len(frame_words) % ADC_WORDS_PER_FRAME:
        raise ValueError(f"ADC frame stream length is not a multiple of {ADC_WORDS_PER_FRAME} u32 words")
    for index in range(0, len(frame_words), ADC_WORDS_PER_FRAME):
        for source in range(ADC_WORDS_PER_FRAME):
            captures[source].append(frame_words[index + source])
    return captures


def split_words(words):
    lo = [signed16(word & 0xFFFF) for word in words]
    hi = [signed16((word >> 16) & 0xFFFF) for word in words]
    return lo, hi


def combine_channel(low_words, high_words):
    samples = []
    for low_word, high_word in zip(low_words, high_words):
        samples.append(signed16(low_word & 0xFFFF))
        samples.append(signed16((low_word >> 16) & 0xFFFF))
        samples.append(signed16(high_word & 0xFFFF))
        samples.append(signed16((high_word >> 16) & 0xFFFF))
    return samples


def build_converter_streams(captures, active_converters=None):
    active = {0, 1, 2, 3} if active_converters is None else set(active_converters)
    streams = {}
    for channel in range(4):
        low_source = 2 * channel
        high_source = low_source + 1
        if channel in active and low_source in captures and high_source in captures:
            streams[f"adc_ch{channel}"] = combine_channel(
                captures[low_source],
                captures[high_source],
            )
    return streams


def diff_stats(values):
    diffs = [b - a for a, b in zip(values, values[1:])]
    positive = sum(1 for diff in diffs if diff > 0)
    negative = sum(1 for diff in diffs if diff < 0)
    zero = sum(1 for diff in diffs if diff == 0)
    signs = [1 if diff > 0 else -1 if diff < 0 else 0 for diff in diffs]
    nonzero = [sign for sign in signs if sign]
    changes = sum(1 for a, b in zip(nonzero, nonzero[1:]) if a != b)
    return positive, negative, zero, changes


def summarize(source, words, presync):
    lo, hi = split_words(words)
    lo_diff = diff_stats(lo)
    hi_diff = diff_stats(hi)
    presync_text = presync.decode("ascii", errors="replace").replace("\r", "").strip()

    lines = [
        f"source {source}",
        f"  words: {len(words)}",
        f"  presync: {presync_text!r}" if presync_text else "  presync: <none>",
        "  first_words: " + " ".join(f"0x{word:08X}" for word in words[:16]),
        (
            f"  lo16: min={min(lo)} max={max(lo)} "
            f"mean={sum(lo) / len(lo):.2f} unique={len(set(lo))}"
        ),
        (
            f"  hi16: min={min(hi)} max={max(hi)} "
            f"mean={sum(hi) / len(hi):.2f} unique={len(set(hi))}"
        ),
        (
            f"  lo_diffs: +={lo_diff[0]} -={lo_diff[1]} "
            f"0={lo_diff[2]} sign_changes={lo_diff[3]}"
        ),
        (
            f"  hi_diffs: +={hi_diff[0]} -={hi_diff[1]} "
            f"0={hi_diff[2]} sign_changes={hi_diff[3]}"
        ),
    ]
    return "\n".join(lines)


def summarize_stream(name, samples):
    sample_diff = diff_stats(samples)
    lines = [
        f"{name}",
        f"  samples: {len(samples)}",
        "  first_samples: " + " ".join(str(sample) for sample in samples[:16]),
        (
            f"  signed16: min={min(samples)} max={max(samples)} "
            f"mean={sum(samples) / len(samples):.2f} unique={len(set(samples))}"
        ),
        (
            f"  diffs: +={sample_diff[0]} -={sample_diff[1]} "
            f"0={sample_diff[2]} sign_changes={sample_diff[3]}"
        ),
    ]
    return "\n".join(lines)


def write_csv(path, captures):
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["source", "index", "word_hex", "lo16_signed", "hi16_signed"])
        for source, words in captures.items():
            for index, word in enumerate(words):
                writer.writerow([
                    source,
                    index,
                    f"0x{word:08X}",
                    signed16(word & 0xFFFF),
                    signed16((word >> 16) & 0xFFFF),
                ])


def write_combined_csv(path, streams):
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["stream", "sample_index", "sample_signed"])
        for name, samples in streams.items():
            for index, sample in enumerate(samples):
                writer.writerow([name, index, sample])


def decimate(values, max_points):
    if len(values) <= max_points:
        return list(range(len(values))), values
    step = (len(values) + max_points - 1) // max_points
    return list(range(0, len(values), step)), values[::step]


def write_plot(path, captures, plot_words, max_points, show, plot_raw_sources, active_converters=None):
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SystemExit("matplotlib is required for plotting: python -m pip install matplotlib") from exc

    streams = build_converter_streams({
        source: words[:plot_words] for source, words in captures.items()
    }, active_converters)
    if streams and not plot_raw_sources:
        fig, axes = plt.subplots(
            len(streams),
            1,
            figsize=(13, max(3.0, 3.0 * len(streams))),
            sharex=True,
            constrained_layout=True,
        )
        if len(streams) == 1:
            axes = [axes]

        for ax, (name, samples) in zip(axes, streams.items()):
            x, sample_plot = decimate(samples, max_points)
            ax.plot(x, sample_plot, label="combined signed16 samples", linewidth=0.9)
            ax.set_title(f"ADC CAPT {name}")
            ax.set_ylabel("signed 16-bit")
            ax.grid(True, alpha=0.25)
            ax.legend(loc="best")

        axes[-1].set_xlabel("ADC sample index")
        fig.savefig(path, dpi=150)
        print(f"Wrote {path}")

        if show:
            plt.show()
        else:
            plt.close(fig)
        return

    fig, axes = plt.subplots(
        len(captures),
        1,
        figsize=(13, max(3.0, 2.7 * len(captures))),
        sharex=True,
        constrained_layout=True,
    )
    if len(captures) == 1:
        axes = [axes]

    for ax, (source, words) in zip(axes, captures.items()):
        lo, hi = split_words(words[:plot_words])
        x_lo, lo_plot = decimate(lo, max_points)
        x_hi, hi_plot = decimate(hi, max_points)
        ax.plot(x_lo, lo_plot, label="lo16", linewidth=0.9)
        ax.plot(x_hi, hi_plot, label="hi16", linewidth=0.9, alpha=0.78)
        ax.set_title(f"ADC CAPT source {source}")
        ax.set_ylabel("signed 16-bit")
        ax.grid(True, alpha=0.25)
        ax.legend(loc="best")

    axes[-1].set_xlabel("capture word index")
    fig.savefig(path, dpi=150)
    print(f"Wrote {path}")

    if show:
        plt.show()
    else:
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(
        description="Capture ADC BRAM data over UART and generate CSV/PNG plots."
    )
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--words",
        type=int,
        default=DEFAULT_FRAMES,
        help="Number of ADC 256-bit frames to capture. Each frame has eight u32 words.",
    )
    parser.add_argument("--sources", default="0,1,2,3,4,5,6,7")
    parser.add_argument(
        "--active-adc-converters",
        "--active-adc-channels",
        dest="active_adc_converters",
        default="0,1,2,3",
        help=(
            "Reconstructed logical ADC channels to write/plot. Channel N is "
            "built from raw source words 2N and 2N+1."
        ),
    )
    parser.add_argument("--command", choices=["CAPT", "PCAP"], default="CAPT")
    parser.add_argument(
        "--program-mode",
        choices=["none", "sine", "triangle", "square-sine", "trapezoid", "byte-pattern"],
        default="none",
        help="Generate and upload a DAC BRAM program before capture.",
    )
    parser.add_argument("--program", help="Upload a binary little-endian u32 or text/CSV DAC program.")
    parser.add_argument("--program-words", type=int, default=MAX_PROGRAM_WORDS)
    parser.add_argument(
        "--program-channel",
        default="all",
        help="DAC program channel to upload: 0..3, comma list, or 'all'.",
    )
    parser.add_argument(
        "--upload-only",
        action="store_true",
        help="Upload the DAC program and exit without running CAPT/PCAP.",
    )
    parser.add_argument(
        "--verify-upload-words",
        type=int,
        default=0,
        help="Read back this many first/last DAC program words per channel after upload.",
    )
    parser.add_argument(
        "--sample-format",
        choices=["twos", "offset"],
        default="twos",
        help="Encoding for generated DAC program samples.",
    )
    parser.add_argument(
        "--sample-rate-mhz",
        type=float,
        default=1000.0,
        help="DAC sample rate used for frequency-generated program patterns.",
    )
    parser.add_argument("--sine-cycles", type=float, default=128.0)
    parser.add_argument("--square-period-words", type=int, default=1024)
    parser.add_argument("--trapezoid-frequency-mhz", type=float, default=5.0)
    parser.add_argument("--byte-high-start", type=lambda x: int(x, 0), default=0x12)
    parser.add_argument("--byte-high-step", type=lambda x: int(x, 0), default=0x11)
    parser.add_argument("--byte-low-start", type=lambda x: int(x, 0), default=0x01)
    parser.add_argument("--byte-low-step", type=lambda x: int(x, 0), default=0x01)
    parser.add_argument("--triangle-step", type=lambda x: int(x, 0), default=0x0100)
    parser.add_argument(
        "--chunk-order",
        type=parse_chunk_order,
        default=[0, 1, 2, 3],
        help=(
            "Permutation of the four 16-bit samples in each 64-bit DAC BRAM "
            "frame. Default is 0,1,2,3."
        ),
    )
    parser.add_argument(
        "--triangle-frequency-mhz",
        type=float,
        help="Generate a triangle with this nominal frequency instead of using --triangle-step.",
    )
    parser.add_argument("--amplitude", type=lambda x: int(x, 0), default=0x3000)
    parser.add_argument("--offset", type=lambda x: int(x, 0), default=0)
    parser.add_argument("--outdir", default="captures")
    parser.add_argument("--prefix", default="adc_capture")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--plot-words", type=int, default=4096)
    parser.add_argument("--max-points", type=int, default=8000)
    parser.add_argument(
        "--rw2",
        type=lambda x: int(x, 0),
        help="Write this RW2 value before upload/capture, for deterministic lane/sample-map tests.",
    )
    parser.add_argument(
        "--expect-build-id",
        type=lambda x: int(x, 0),
        help="Fail unless selector 3 reports this build ID before capture.",
    )
    parser.add_argument(
        "--plot-raw-sources",
        action="store_true",
        help="Plot raw capture sources as lo16/hi16 instead of reconstructed logical ADC channels.",
    )
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    if args.words <= 0 or args.words > MAX_CAPTURE_FRAMES:
        raise ValueError(f"--words must be 1..{MAX_CAPTURE_FRAMES} ADC frames")
    if args.plot_words <= 0:
        raise ValueError("--plot-words must be positive")
    if args.program_words <= 0 or args.program_words > MAX_PROGRAM_WORDS:
        raise ValueError(f"--program-words must be 1..{MAX_PROGRAM_WORDS}")
    if args.verify_upload_words < 0:
        raise ValueError("--verify-upload-words must be non-negative")
    if args.program and args.program_mode != "none":
        raise ValueError("use either --program or --program-mode, not both")
    if args.upload_only and not (args.program or args.program_mode != "none"):
        raise ValueError("--upload-only requires --program or --program-mode")

    sources = parse_sources(args.sources)
    active_adc_converters = parse_adc_converters(args.active_adc_converters)
    program_channels = parse_program_channels(args.program_channel)
    program = make_program(args)
    command_name = "PCAP" if program and args.command == "CAPT" else args.command
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    captures = {}
    summaries = []
    started = time.time()

    with serial.Serial(args.port, args.baud, timeout=args.timeout) as port:
        time.sleep(0.2)
        port.reset_input_buffer()
        if args.expect_build_id is not None:
            check_build_id(port, args.expect_build_id)
        if args.rw2 is not None:
            set_rw2(port, args.rw2)
        if program:
            for channel in program_channels:
                print(f"Uploading {len(program)} DAC program words to DAC channel {channel}...")
                upload_program(port, program, channel)
                verify_program_upload(port, program, channel, args.verify_upload_words)
        if args.upload_only:
            print("Upload complete; skipping capture.")
            return
        print(f"Capturing {args.words} ADC 256-bit frames with {command_name}...")
        presync, frame_words = capture_frames(port, command_name, args.words)
        all_captures = split_frame_captures(frame_words)
        for source in sources:
            captures[source] = all_captures[source]
            summary = summarize(source, captures[source], presync if source == sources[0] else b"")
            summaries.append(summary)
            print(summary)

    source_tag = "_".join(str(source) for source in sources)
    csv_path = outdir / f"{args.prefix}_sources_{source_tag}.csv"
    combined_csv_path = outdir / f"{args.prefix}_sources_{source_tag}_combined.csv"
    png_path = outdir / f"{args.prefix}_sources_{source_tag}.png"
    summary_path = outdir / f"{args.prefix}_sources_{source_tag}_summary.txt"
    streams = build_converter_streams(captures, active_adc_converters)

    write_csv(csv_path, captures)
    print(f"Wrote {csv_path}")
    if streams:
        write_combined_csv(combined_csv_path, streams)
        print(f"Wrote {combined_csv_path}")
        summaries.append(
            "combined streams note: each logical adc_chN is reconstructed from "
            "raw source words 2N and 2N+1 in the 256-bit capture frame."
        )
        for name, samples in streams.items():
            summaries.append(summarize_stream(name, samples))
    summary_path.write_text("\n\n".join(summaries) + "\n")
    print(f"Wrote {summary_path}")
    write_plot(
        png_path,
        captures,
        min(args.plot_words, args.words),
        args.max_points,
        args.show,
        args.plot_raw_sources,
        active_adc_converters,
    )

    elapsed = time.time() - started
    print(f"Done in {elapsed:.1f}s")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
