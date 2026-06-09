import argparse
import csv
import re
import struct
import sys
import time
from pathlib import Path

try:
    import serial
except ImportError:
    print("pyserial is required: python -m pip install pyserial", file=sys.stderr)
    raise


SYNC_WORD = b"\xFE\x10\xCA\xFE"
DEFAULT_FRAMES = 4096
ADC_WORDS_PER_FRAME = 8
MAX_PROGRAM_WORDS = 8192


def signed16(value):
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


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
    while True:
        byte = port.read(1)
        if not byte:
            raise TimeoutError("timed out waiting for capture sync word")
        window.extend(byte)
        if len(window) > len(SYNC_WORD):
            del window[0]
        if bytes(window) == SYNC_WORD:
            return


def read_line(port):
    line = bytearray()
    while True:
        byte = port.read(1)
        if not byte:
            raise TimeoutError("timed out waiting for UART line")
        line.extend(byte)
        if byte == b"\n":
            return bytes(line)


def wait_for_line_prefix(port, prefix):
    while True:
        line = read_line(port)
        text = line.decode("ascii", errors="replace").strip()
        if text.startswith(prefix):
            return text


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


def parse_u32_token(token):
    token = token.strip()
    if not token:
        raise ValueError("empty token")
    return int(token, 0) & 0xFFFFFFFF


def load_text_program(path):
    words = []
    numeric = re.compile(r"^(?:0x[0-9a-fA-F]+|[0-9]+)$")
    with open(path, newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            if row[0].strip().startswith("#"):
                continue
            candidates = [cell.strip() for cell in row]
            if len(candidates) > 1 and numeric.match(candidates[1]):
                words.append(parse_u32_token(candidates[1]))
                continue
            for candidate in candidates:
                if numeric.match(candidate):
                    words.append(parse_u32_token(candidate))
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


def default_triangle_program(words, step):
    sample = 0
    rising = True
    out = []

    for _ in range(words):
        pair = []
        for _ in range(2):
            pair.append(sample & 0xFFFF)
            if rising:
                if sample >= 0xFFFF - step:
                    sample = 0xFFFF
                    rising = False
                else:
                    sample += step
            else:
                if sample <= step:
                    sample = 0
                    rising = True
                else:
                    sample -= step
        out.append((pair[1] << 16) | pair[0])
    return out


def upload_program(port, words, channel):
    command = f"PROG {channel} {len(words)}\n".encode("ascii")
    port.write(command)
    port.flush()
    line = wait_for_line_prefix(port, "PGRD")
    print(line)
    port.write(struct.pack(f"<{len(words)}I", *words))
    port.flush()
    print(wait_for_line_prefix(port, "OK PROG"))
    frame_count = len(words) // 2
    rw3_value = (frame_count << 8) & 0xFFFFFF00
    port.write(f"WRTE 3 0x{rw3_value:08X}\n".encode("ascii"))
    port.flush()
    print(wait_for_line_prefix(port, "OK"))
    print(f"Set DAC BRAM loop frame_count={frame_count} via RW3=0x{rw3_value:08X}")


def capture(port, frames, use_program):
    command_name = "PCAP" if use_program else "CAPT"
    command = f"{command_name} {frames}\n".encode("ascii")
    port.write(command)
    wait_for_sync(port)
    return read_exact(port, frames * ADC_WORDS_PER_FRAME * 4)


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


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Capture logical adc_ch0..3 BRAM samples from the live loopback stream and write the "
            "capture to CSV. Optionally upload a DAC BRAM program and capture that "
            "programmed output instead."
        )
    )
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--words", type=int, default=DEFAULT_FRAMES, help="ADC 256-bit frames to capture")
    parser.add_argument("--source", type=int, default=0, choices=range(8), help="deprecated; capture now always records all eight frame words")
    parser.add_argument("--program", help="binary little-endian u32 or text/CSV DAC program")
    parser.add_argument("--program-words", type=int, default=MAX_PROGRAM_WORDS)
    parser.add_argument("--program-channel", default="all", help="DAC program channel: 0..3, comma list, or 'all'")
    parser.add_argument("--upload-only", action="store_true", help="upload the DAC program and exit")
    parser.add_argument("--triangle-step", type=lambda x: int(x, 0), default=0x0100)
    parser.add_argument(
        "--upload-triangle",
        action="store_true",
        help="upload a generated DAC BRAM triangle instead of capturing the live generator",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="deprecated compatibility flag; live capture is already the default",
    )
    parser.add_argument("--out", default="adc_capture.csv")
    parser.add_argument("--timeout", type=float, default=120.0)
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
    args = parser.parse_args()

    if args.words <= 0 or args.words > DEFAULT_FRAMES:
        raise ValueError(f"--words must be 1..{DEFAULT_FRAMES} ADC frames")
    if args.program_words <= 0 or args.program_words > MAX_PROGRAM_WORDS:
        raise ValueError(f"--program-words must be 1..{MAX_PROGRAM_WORDS}")

    if args.program:
        program_words = load_program(args.program)
        if len(program_words) > MAX_PROGRAM_WORDS:
            raise ValueError(f"program has {len(program_words)} words; max is {MAX_PROGRAM_WORDS}")
    elif args.upload_triangle and not args.no_upload:
        program_words = default_triangle_program(args.program_words, args.triangle_step)
    else:
        program_words = []
    if program_words and len(program_words) % 2:
        raise ValueError("DAC 64-bit program frames require an even number of u32 words")
    if args.upload_only and not program_words:
        raise ValueError("--upload-only requires --program or --upload-triangle")

    started = time.time()
    program_channels = parse_program_channels(args.program_channel)

    with serial.Serial(args.port, args.baud, timeout=args.timeout) as port:
        port.reset_input_buffer()
        if args.expect_build_id is not None:
            check_build_id(port, args.expect_build_id)
        if args.rw2 is not None:
            set_rw2(port, args.rw2)
        if program_words:
            for channel in program_channels:
                print(f"Uploading {len(program_words)} DAC program words to DAC channel {channel}...")
                upload_program(port, program_words, channel)
        if args.upload_only:
            print("Upload complete; skipping capture.")
            return
        print(f"Capturing {args.words} ADC frames...")
        raw = capture(port, args.words, use_program=bool(program_words))

    with open(args.out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["frame", "source", "word_hex", "lo16", "hi16", "lo16_signed", "hi16_signed"])
        for index in range(args.words * ADC_WORDS_PER_FRAME):
            word = struct.unpack_from("<I", raw, index * 4)[0]
            lo = word & 0xFFFF
            hi = (word >> 16) & 0xFFFF
            writer.writerow([
                index // ADC_WORDS_PER_FRAME,
                index % ADC_WORDS_PER_FRAME,
                f"0x{word:08X}",
                lo,
                hi,
                signed16(lo),
                signed16(hi),
            ])

    elapsed = time.time() - started
    print(f"{args.words} captured frames written to {args.out} in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
