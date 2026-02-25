# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

REMUS-100 AUV Run Log File (RLF) parser. All field offsets, scaling factors, and physical
interpretations were derived empirically — no official Hydroid/Kongsberg documentation was used.
Dataset: Makua Beach, O'ahu, 2013 (doi:10.6075/J09P3042).

## Commands

```bash
# Parse and print summary
python remus_rlf.py 130906/RLF/130906.RLF

# Parse and generate both diagnostic plots
python remus_rlf.py 130906/RLF/130906.RLF --plot

# Parse ADCP companion files
python remus_adcp.py 130906/ADCP/ --plot

# After plot changes, copy outputs to repo root for README
cp 130906/RLF/130906_summary.png rlf_final_comprehensive.png
cp 130906/RLF/130906_quality.png rlf_quality.png

# Push (token stored in /tmp/gh_token)
TOKEN=$(cat /tmp/gh_token) && git -c url.https://isaacgerg:${TOKEN}@github.com.insteadOf=https://github.com push origin main
```

No test suite, no build system. Dependencies: `numpy`, `matplotlib` (optional, for `--plot`).

## Architecture — remus_rlf.py

Three-layer pipeline:

**1. `parse_raw_records(data: bytes) → dict[int, list[bytes]]`**
Single pass over the raw file. Scans for `0xEB 0x90` magic bytes, reads the 8-byte header,
extracts the payload, and groups payloads by record type integer. Returns
`{record_type_int: [payload_bytes, ...]}`. No decoding happens here.

**2. `_DECODERS` dispatch table + `decode_*` functions**
Each `decode_*(payloads: list[bytes]) → dict` function takes the raw payload list for one
record type and returns a dict of named numpy arrays (or a list of dicts for variable-structure
records like waypoints and battery status). All time series expose a `t_hrs` field (hours from
first sample) computed by `unwrap_timestamps()`, which handles the midnight UTC rollover in the
uint32 ms-since-midnight timestamps.

Exception: `Acoustic Modem Log` (0x0424) has no embedded timestamp. Its `t_hrs` is injected by
`parse_rlf` after decoding, via `_stamp_by_position()`, which infers time by interpolating the
file-byte positions of modem records against surrounding Navigation record positions.

**3. `parse_rlf(filepath) → dict`**
Calls `parse_raw_records`, runs every known type through `_DECODERS`, injects modem timestamps,
and returns a unified dict keyed by human-readable record name (e.g. `'Navigation'`, `'YSI CTD'`).
Also contains `'_raw'` (raw grouped payloads) and `'_summary'` (counts + sizes).

**Plotting** lives entirely in the `__main__` block (not inside `parse_rlf`). The `--plot` flag
generates two files next to the input:
- `*_summary.png` — 4×2 grid: track, depth profile, T/S, speed of sound, ECO, attitude, sidescan bathymetry, nav speed
- `*_quality.png` — 4×1 grid: sensor record rate, DVL bottom lock + acoustic fix markers, battery pack voltage, acoustic modem quality scores

## Architecture — remus_adcp.py

Parses the three companion file types in an ADCP directory:
- `.ADC` — standard RDI PD0 binary format (ensemble-based, per-bin velocities)
- `.GPS` — ASCII position log (~1 Hz)
- `.rmf` — REMUS Run Mission File (INI-style)

The PD0 parser uses a fixed-offset ensemble structure: each ensemble starts with a 2-byte header
count, then N offset pointers, each pointing to a data type block (Fixed Leader, Variable Leader,
Velocity, Correlation, Echo Intensity, Percent-Good, Bottom Track, plus a custom REMUS Navigation
block at type 0x2000).

## Adding a New Record Decoder

1. Add a `REC_*` constant and a `RECORD_NAMES` entry.
2. Write a `decode_*(payloads)` function following the existing pattern — iterate payloads,
   use `struct.unpack_from`, call `unwrap_timestamps()` on the raw timestamp array.
3. Register it in `_DECODERS`.
4. Update `REMUS_RLF_FORMAT_SPEC.txt` (section 3 summary table + new section 4.N).

## Key Facts for Binary Archaeology

- All integers/floats are little-endian.
- Standard timestamp location: `uint32 LE` at payload offset 16 (ms since midnight UTC, bit 31 masked).
- Positions are `float64` (lat/lon) in high-rate records, `float32` in lower-rate records.
- Sentinel for invalid ADCP/sidescan data: `-32.768` (float32 encoding of int16 `0x8000`).
- All 32 observed record types are now decoded. The module-level docstring, `RECORD_NAMES` dict,
  and `REMUS_RLF_FORMAT_SPEC.txt` are kept in sync.

## Data

Raw `.RLF` files are not in the repo. Download from UCSD Library (doi:10.6075/J09P3042).
Best run for testing: **130906** (fewest thruster/motor reliability issues).

## Maintenance Notes

- Keep `REMUS_RLF_FORMAT_SPEC.txt` in sync with any decoder changes.
- License: AGPL v3 + additional permission for accredited universities and DoD-recognized UARCs
  (non-commercial use only, no obligation to release modifications). See `LICENSE`.
