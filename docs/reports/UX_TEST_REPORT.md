# UX Test Report

Date: 2026-11-12. Scope: the interactive workflow added in U1–U7 (regions of interest,
plot modes, region fitting and model comparison, automatic search and candidate
classes, Nuclear Data Explorer, assisted calibration, workflow panel and modes).

## 1. What was tested, and how

| Kind | Evidence | Result |
|---|---|---|
| Automated interface tests (pytest-qt, offscreen) with synthesised mouse events (press/move/release on the plot viewport), key clicks and widget actions | `tests/integration/test_gui_roi.py`, `test_gui_nuclear_data.py`, `test_gui_assisted_calibration.py`, `test_gui_provenance.py`, `test_gui_workflow.py` and the earlier GUI tests | all pass (see the test run in `README.md`) |
| Rendering at **1366 × 768** and **1920 × 1080** on the real Windows display (`scripts/dev/ux_screenshots.py`): window minimum size, screenshots of Spectrum Explorer (Beginner and Advanced), Nuclear Data Explorer with reference lines, Energy Calibration | screenshots in the build folder (not committed: they are regenerated on demand) | fits both sizes after the fixes below |
| **Manual walk-through by a person with real mouse and keyboard** | — | **not yet done** (owner action, §4) |

Programmatic tests and rendered screenshots are *not* a validation of the interface by
a user; that needs §4.

## 2. Findings and fixes

| # | Finding (at 1366 × 768) | Fix |
|---|---|---|
| 1 | Window minimum width 1579 px: wider than the screen. Causes: long button rows on the Regions panel and the Nuclear Data Explorer, and the new workflow panel | rows split, combo boxes shortened, workflow list may shrink, every page placed in a scroll area; minimum window size now 209 × 259 |
| 2 | Spectrum plot squeezed to a few pixels when the Regions tab was open (the tab's content claimed the height) | Regions tab scrolls inside its tab; plot minimum height 300 px |
| 3 | In Beginner mode the labels "Sensitivity:" and "Continuum channels:" stayed visible while their controls were hidden | labels hidden with their controls |

## 3. Observations (no change yet)

- At full spectrum width a region of ~30 channels is a thin band; zoom (wheel, Z) is
  needed to work with it. Selecting a row in the regions table zooms to the region.
- The workflow panel takes vertical space under the page list; the splitter between
  them can be dragged.
- The mode (Beginner/Advanced) is not remembered between sessions.
- Dark Windows theme: all text readable in the screenshots; the plot keeps a white
  background by design (print-like).

## 4. Manual walk-through — script for the owner (≈ 20 minutes)

At 1366 × 768 (or Windows display scaling that gives this size) and at 1920 × 1080:

1. Start NSW; open `data\synthetic\sources` (or the installed *Demo spectra*).
2. Spectrum Explorer: select `syn-x-sample`. Markers appear (colours by class).
3. Press **R**, drag across a peak; drag the region's edges; **Ctrl+Z**, **Ctrl+Y**.
4. Press **F**: the fit and residuals appear in the Regions tab; read the limits line.
5. Switch to **Advanced**; *Compare models*; *Test low-energy tail* (wait for the result).
6. **S**, click a marker, *Exclude selected*; *Restore excluded*.
7. Right-click on the plot: try each menu entry.
8. Nuclear Data Explorer: select SYN-X, *Show lines*, **S** + click a fitted peak marker,
   *Match selected peak*, select a row, *Accept assignment*.
9. Energy Calibration on `syn-cal-source`: enter gain/offset, *Suggest assignments*, fit.
10. File → New project, *Save session*, close NSW, reopen the project: regions and
    decisions are back.

Record for each step: worked / confusing / failed, with a screenshot if something is
wrong. Results go into this section.
