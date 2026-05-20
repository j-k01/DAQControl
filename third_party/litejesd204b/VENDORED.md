# LiteJESD204B Source

This directory vendors the LiteJESD204B generator source used to produce the
checked-in DAC TX RTL.

- Upstream: https://github.com/enjoy-digital/litejesd204b
- Commit: `193f4d870277c6a1c5daa86017c97046757dfe63`
- License: BSD-2-Clause, see `LICENSE`

The FPGA project does not synthesize Python, Migen, or LiteX. Those packages
are generator-time dependencies only for `scripts/gen_litejesd_dac_tx.py`.
