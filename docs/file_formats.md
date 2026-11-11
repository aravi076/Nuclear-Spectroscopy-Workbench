# File Formats

Formats observed in real data are documented from inspection; others are planned.

## Lynx CSV export (`.csv`) — observed

UTF-8, CRLF. Lines:

```
Start Time, Mon Apr 27 14:49:03 GMT+0530 2026
Energy calibration,  Offset: <a>, Slope: <b>, Quadratic: <c>
Live Time (s), <float>
Real Time (s), <float>
Elapsed Computational, <int>
Spectrum
Channel, Energy (keV), Counts
1, <energy>, <counts>
…
8192, <energy>, <counts>
```

Channels are 1-based; `energy = a + b·channel + c·channel²`. The calibration is whatever
was stored in the MCA and may be invalid.

## Lynx XML (`.ava`) — observed

Root `<mcadata xmlns="http://www.canberra.com/mcaanywhair">`.

- `<parameter code="…" name="…" value="…" input="…"/>` × ~150: hardware and acquisition
  settings. `(code, name)` is not unique (e.g. two "Detector Name": code 81 = MCA name,
  code 0 = operator's detector label; two "Status", two "Conversion Gain").
- `<spectrum start start_time_ms elapsed_live elapsed_real elapsed_deadtime …>` with
  comma-separated counts as text (first value = channel 1). `elapsed_deadtime` is in %.
- `<calibration_details type="energy|fwhm|low_energy_tail">` with `<model type=…>` and
  `<coefficient value=…/>` children; covariance elements are empty in the audited files.
- `<display_region>`, `<analysis_settings>` (embedded nuclide library), `<analysis_results>`.

## Canberra CAM (`.cnf`) — observed, reverse-engineered

Binary, little-endian. Directory of 48-byte entries from offset 0x70; entry: uint32
section id at +0, uint32 section offset at +0x0A.

| Section id | Content | Fields used |
|---|---|---|
| 0x00012000 | Acquisition parameters | uint16 at +0x22 → record offset *r*, uint16 at +0x24 → time offset *t* (both relative to section + 0x30). Channels = byte at +0xBA × 256. Energy coefficients: 4 PDP-11 floats at *r* + 0x44. Start time: uint64 at *t* + 0x01 (100 ns ticks since 1858-11-17, local wall-clock). Real / live time: uint64 at *t* + 0x09 / *t* + 0x11, stored as two's-complement negative 100 ns ticks. |
| 0x00012001 | Sample | name at +0x30 (64 bytes) |
| 0x00012005 | Channel data | uint32 counts from +0x200 |

PDP-11 float: swap the two 16-bit words, read as IEEE float32, divide by 4.
In the audited files real and live times are whole seconds. Other sections (analysis
parameters, nuclide library) are not decoded.

## NSW synthetic spectrum (`*.nswsyn.json`) — written by NSW

UTF-8 JSON, one spectrum per file. `"format": "nsw-synthetic-spectrum"` is the first key
so the type is recognisable from the head of the file.

| Key | Content |
|---|---|
| `format`, `version` | `nsw-synthetic-spectrum`, `1` |
| `synthetic_id`, `detector_id` | identifiers |
| `live_time_s`, `real_time_s` | seconds |
| `truth` | ground truth: role (`source`/`background`), detector type, seed, generator and NumPy version, energy scale, resolution model, per-line truth (energy, centroid channel, FWHM, expected and realised peak and continuum counts), background model with expected and realised counts, data hash, list of model assumptions |
| `counts` | array of integers, one per channel (channel 1 first) |

## NSW spectrum CSV export — written by NSW

Comment lines starting with `#` (software version, origin, kind, source file and
hashes, times, processing history, calibration statement), then a header line and one
row per channel: `channel,value,uncertainty`. An `energy_keV` column is written only for
a spectrum with a verified calibration.

## Not supported

Generic CSV/TXT, IAEA/Ortec `.spe`, Ortec `.chn`, Amptek `.mca`: no real files of these
formats have been inspected, so no adapters exist and such files are rejected with an
explicit message.
