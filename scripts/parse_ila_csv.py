#!/usr/bin/env python3
"""Parse a Vivado ILA CSV (write_hw_ila_data -csv_file) and report, per probe
column, whether it is active (multiple distinct values) or stuck, plus a few
sample values.  Used to localize the current-monitor datapath bug:

  probe0 dac_tx_control_debug   (low byte carries dac_src_sel_tx[7:0])
  probe2 dac_debug_source_words (src_converter = crossbar output, all 4 DACs)
  probe5 mon_words_tx           (scaled current-monitor words, pre-crossbar)
  probe6 {0, izh_i_mon_gtdbg}   (raw per-neuron i_mon, GT-sampled)
"""

from __future__ import annotations

import csv
import sys
from collections import OrderedDict


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "ila_gth_tx.csv"
    rows = list(csv.reader(open(path, newline="")))
    # find the header row (contains "Sample in Buffer")
    hdr_i = next((i for i, r in enumerate(rows)
                  if any("Sample in Buffer" in c for c in r)), 0)
    header = rows[hdr_i]
    data = [r for r in rows[hdr_i + 1:] if len(r) == len(header) and r]

    cols = OrderedDict((name.strip(), idx) for idx, name in enumerate(header))
    print(f"{len(data)} samples, {len(header)} columns")
    print("columns:", [c for c in cols])

    meta = {"Sample in Buffer", "Sample in Window", "TRIGGER"}
    for name, idx in cols.items():
        if name in meta:
            continue
        vals = [r[idx].strip() for r in data]
        distinct = list(dict.fromkeys(vals))
        nz = [v for v in vals if set(v) - {"0", "x", "X", " "}]
        flag = "ACTIVE" if len(distinct) > 1 else "STUCK "
        allzero = " ALL-ZERO" if not nz else ""
        sample = distinct[:6]
        print(f"  [{flag}] {name:32s} distinct={len(distinct):4d}{allzero}  e.g. {sample}")


if __name__ == "__main__":
    main()
