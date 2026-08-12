"""Directory schema and raw-data writers for optical sweep experiments."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Mapping

import numpy as np


SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def experiment_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name).strip()).strip("._-")
    return slug[:80] or "optical_sweep"


def _jsonable(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def write_json(path: Path, data: Mapping) -> None:
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_jsonable(data), indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    temporary.replace(path)


def load_manifest(experiment_dir: Path) -> dict:
    path = Path(experiment_dir) / "experiment.json"
    return json.loads(path.read_text(encoding="utf-8"))


def create_experiment(root: Path, name: str, metadata: Mapping) -> Path:
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = root / f"{stamp}_{experiment_slug(name)}"
    experiment_dir = base
    suffix = 2
    while experiment_dir.exists():
        experiment_dir = Path(f"{base}_{suffix}")
        suffix += 1
    experiment_dir.mkdir()
    manifest = {
        "schema": "daq_optical_sweep",
        "schema_version": SCHEMA_VERSION,
        "experiment_name": str(name).strip(),
        "experiment_id": experiment_dir.name,
        "created_utc": utc_now(),
        "capture_status": "running",
        "completed_heater_captures": 0,
        "heater_captures": [],
    }
    manifest.update(_jsonable(dict(metadata)))
    write_json(experiment_dir / "experiment.json", manifest)
    return experiment_dir


def update_manifest(experiment_dir: Path, **updates) -> dict:
    manifest = load_manifest(experiment_dir)
    manifest.update(_jsonable(updates))
    write_json(Path(experiment_dir) / "experiment.json", manifest)
    return manifest


def save_heater_capture(
    experiment_dir: Path,
    *,
    index: int,
    voltage_v: float,
    direction: int,
    stacks: Mapping[int, np.ndarray],
    capture_meta: Mapping | None = None,
) -> Path:
    """Save one heater-voltage capture with every raw ADC stack."""

    arrays = {int(channel): np.asarray(values, dtype=np.int16)
              for channel, values in stacks.items()}
    if sorted(arrays) != [0, 1, 2, 3]:
        raise ValueError("heater capture must contain ADC channels 0 through 3")
    shapes = {values.shape for values in arrays.values()}
    if len(shapes) != 1:
        raise ValueError("all ADC channel stacks must have the same shape")
    repetitions, samples = next(iter(shapes))
    if repetitions < 1 or samples < 1:
        raise ValueError("raw channel stacks cannot be empty")

    direction_name = "reverse" if int(direction) else "forward"
    voltage_label = f"{float(voltage_v):.4f}".replace(".", "p")
    point_name = f"heater_{int(index):03d}_{direction_name}_{voltage_label}V"
    point_dir = Path(experiment_dir) / point_name
    point_dir.mkdir(parents=False, exist_ok=False)
    meta = {
        "schema": "daq_optical_heater_capture",
        "schema_version": SCHEMA_VERSION,
        "index": int(index),
        "heater_voltage_v": float(voltage_v),
        "direction": direction_name,
        "direction_code": int(direction),
        "captured_utc": utc_now(),
        "repetitions": int(repetitions),
        "samples_per_channel": int(samples),
        "raw_file": "raw_captures.npz",
        "capture_meta": _jsonable(dict(capture_meta or {})),
    }
    np.savez_compressed(
        point_dir / "raw_captures.npz",
        artifact_kind="raw_trigger_aligned_optical_heater_capture",
        synthetic=np.bool_(False),
        fs_hz=np.float64(1.0e9),
        heater_voltage_v=np.float64(voltage_v),
        direction=np.int8(direction),
        capture_meta_json=json.dumps(meta["capture_meta"], sort_keys=True),
        **{f"raw_ch{channel}": values for channel, values in arrays.items()},
    )
    write_json(point_dir / "heater.json", meta)

    manifest = load_manifest(experiment_dir)
    heater_captures = list(manifest.get("heater_captures", []))
    heater_captures.append({
        "index": int(index),
        "heater_voltage_v": float(voltage_v),
        "direction": direction_name,
        "directory": point_name,
    })
    update_manifest(
        experiment_dir, heater_captures=heater_captures,
        completed_heater_captures=len(heater_captures))
    return point_dir
