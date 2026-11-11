# User Manual

Covers the seven pages of the application: dashboard, spectrum explorer, background
subtraction, peak analysis, peak fitting, energy calibration and resolution analysis.
Efficiency, activity and nuclide candidates exist as calculation engines without a
page; project files and reports are not available yet.

## Starting

```powershell
.venv\Scripts\python.exe -m nsw.ui.app            # empty start
.venv\Scripts\python.exe -m nsw.ui.app --load     # load the folders in config\local.yaml
```

## Project Dashboard

- **Open experimental dataset folder…** reads every file below the chosen folder. The
  folder is only read; NSW never writes into it. Reading runs in the background and the
  status bar shows progress.
- The summary lists files imported, unsupported and malformed files, distinct spectra,
  acquisitions, independent measurements and intermediate saves.
- **Findings** lists what NSW noticed about the data: successive saves of one
  acquisition, files with the same name but different content, file names that disagree
  with the recorded live time, duplicates, and copies that disagree on times.
- **Verify dataset integrity** compares the folder with the stored SHA-256 manifest.
- **Open synthetic dataset folder…** loads NSW synthetic spectra (`data\synthetic`).
  A folder containing both kinds is refused.

## Spectrum Explorer

Three lists: **Raw spectra**, **Background spectra**, **Synthetic spectra**.

- Each row is one distinct spectrum; the *Files* column names every file holding the
  same data. Intermediate saves appear in grey beneath the final save of their
  acquisition.
- Select a row to display it. Select several rows (Ctrl/Shift) to overlay up to eight.
- Mouse: drag to pan, wheel to zoom, right-drag to zoom an axis; **Reset view** restores
  the full range. Moving the mouse shows the channel and its contents ± √N.
- **Logarithmic counts axis** draws empty channels at the axis floor (display only).
- **Show count rate** divides by live time so spectra of different duration can be
  compared.
- The x-axis is the channel number. The red banner
  “ENERGY CALIBRATION NOT VERIFIED — ENERGY VALUES MUST NOT BE USED FOR QUANTITATIVE
  ANALYSIS.” is shown whenever a displayed spectrum has no verified calibration, which
  is currently every experimental spectrum.
- Tabs below the plot: measurement metadata (including what is missing), detector and
  hardware settings, original file with hashes, warnings and processing history.
- **File → Export selected spectrum as CSV / Export plot as PNG.** Exports are refused
  inside an experimental dataset folder.

### Designating a background

NSW does not decide from a file name that a spectrum is a background. Right-click an
independent measurement in *Raw spectra* and choose **Designate as background
measurement**. It then appears under *Background spectra*; the original stays unchanged.
Intermediate saves cannot be designated. Designations last for the session only.

## Background Subtraction

1. Choose the gross spectrum and a designated background.
2. The message box shows the validity check. “REFUSED” lines block the calculation
   (for example: same acquisition, different channel count, experimental mixed with
   synthetic). “Caution” lines are assumptions NSW cannot check from the files.
3. **Check and subtract** computes N_net = N_gross − α·N_background with
   σ² = N_gross + α²·N_background, α = live time ratio. The plot shows gross, scaled
   background and net; negative net values are kept.
4. The *Result* tab lists the equation, α, totals with statistical and systematic
   uncertainty, and the assumptions. **Export net spectrum as CSV…** writes the values
   with uncertainties and the full processing history.
5. *Comparison of designated backgrounds* tests each pair of independent experimental
   backgrounds for equal count rates (χ² over 64-channel blocks).

## Peak Analysis (automatic peak search)

1. Choose a spectrum. Enter the detection scales: the peak widths (FWHM, in channels)
   you expect, for example `4, 8`. A coarse ladder is enough. **Suggest from strongest
   peak** measures the most prominent peak and proposes its width and twice its width.
2. Set the significance threshold (default 4), the minimum separation and, if wanted, a
   channel region. **Search for peaks** runs in the background.
3. Candidates are marked on the plot and listed with position, position uncertainty,
   significance and estimates of width, area and height. Selecting a row zooms to the
   candidate. Candidates closer than 3 FWHM share a *fit group*.

A candidate is not a confirmed peak: Compton edges and other sharp features of the
continuum also respond, and lines closer than about one FWHM appear as one candidate.
Positions are channel numbers; an energy column appears only for spectra with a verified
calibration (at present only synthetic spectra).

## Peak Fitting

1. Choose the spectrum. Either pick a candidate group (region, positions and width
   guesses are filled in) or enter the fit region, the peak positions and a FWHM guess
   by hand.
2. Estimator:
   - *Automatic*: Poisson likelihood for measured counts, weighted least squares for net
     spectra.
   - *Poisson likelihood*: only for measured counts; works with empty channels.
   - *Weighted least squares*: for net spectra; refused if a channel has zero variance.
   - *Joint fit with a designated background*: fits the gross spectrum together with an
     independent background measurement instead of subtracting it first. Needs a
     designated background (Spectrum Explorer).
3. Continuum: linear, constant or none. *Step under each peak* adds a small step on the
   low side; use it only for strong HPGe peaks where the residuals show it is needed.
   *Common width* gives all peaks of the region the same width.
4. **Fit region** fits the entered region; **Fit all candidate groups** fits every group
   of the last search. The plot shows data, model and continuum, with normalised
   residuals below. Selecting a table row shows that fit.
5. The *Quality* column must be read before any number is used:
   - `ok`: converged, usable uncertainties, statistically acceptable.
   - `POOR FIT`: the model does not describe the data (probability below 0.001). The
     values are not reliable. Typical causes: a neighbouring line or edge in the region,
     X-ray multiplets, the low-energy tail of a very strong peak.
   - `FLAGGED`: a parameter ended on a bound or the covariance is unusable.
   - `NOT CONVERGED`.
6. **Assess multiplet** (two or more positions) compares the fit with one component
   fewer. "Supported" means the data statistically prefer the extra component; it is not
   proof that two lines are resolved. Check the separation in FWHM and the correlation
   of the areas, shown in the details tab.
7. **Export fitted peaks as CSV…** writes all fits of the spectrum with uncertainties,
   χ², quality and provenance. Results of experimental spectra are labelled
   *provisional, channel units* until an energy calibration has been verified.

Uncertainties are one standard deviation from the fit covariance and are not rescaled
by χ². Peaks with area/uncertainty below 3 are reported as not significant.

## Energy Calibration

1. Fit the calibration peaks on the Peak Fitting page first. The page lists every
   usable fitted peak of the chosen spectrum (converged, unflagged, significant).
2. Tick the peaks to use. For each, type the reference energy (keV), its uncertainty and
   **where the value comes from**; a label is optional. NSW supplies no reference
   energies and refuses a point without a source.
3. Choose the model (linear unless a higher order is justified) and press
   **Fit calibration**. The plot shows the curve with its 1 σ band and the residuals
   (reference − fitted, keV, with error bars); the tabs show coefficients with
   uncertainties, χ², the valid range and every point with its residual.
4. **Test next higher order** says whether the extra coefficient is significant.
5. *Multiply covariance by χ²/ndf* is for real scatter that the stated uncertainties do
   not cover (for example electronic non-linearity). It is not a remedy for a wrong
   line assignment; look at the normalised residuals first.
6. Status:
   - Synthetic spectrum, at least order + 2 consistent points: **verified**.
   - Two points for a line (or order + 1 in general): fitted, but it cannot be checked
     and is never verified.
   - Experimental spectrum: always **provisional** at present. The spectrum keeps its
     channel axis until the laboratory has confirmed the line assignments and the
     source of the reference energies.
7. Every fit is added to the *Calibration history* of the session. **Export
   calibration as CSV…** writes coefficients, covariance, range and all points.
8. *Stability of a peak position* compares the fitted position of the peak nearest to a
   given channel across all independent spectra that have a fit there. Use it with
   peaks you know to be the same line. If the positions disagree, the gain drifted and
   one calibration does not fit all runs.

## Resolution Analysis

1. Fit peaks first. The page lists the peaks of statistically acceptable fits whose
   width was fitted (not held fixed).
2. Untick every peak that is not a single full-energy line (multiplets, annihilation
   or otherwise broadened peaks). Their width does not measure the resolution.
3. Abscissa: *Channel* (FWHM in channels) is always available. *Energy in keV* appears
   only for spectra with a verified energy calibration.
4. **Fit resolution model** fits FWHM² = a + b·x; tick *Include the c·x² term* only if
   the residuals show a trend. A term the data do not support is set to 0 and reported.
5. The plot shows the widths with error bars, the model with its 1 σ band and the
   normalised residuals; the tabs give coefficients, χ², the valid range, predicted
   widths (and FWHM/E in percent when in keV) and every point.
6. If the status says the widths are not consistent with the model, look for the peak
   with the largest residual: it is probably not a single line.
7. Experimental results are provisional and in channel units for now.

### Fitting peaks of known shape

On the Peak Fitting page, *Hold positions fixed* and *Hold width fixed at the FWHM guess*
keep those values at what you entered. Use this for weak peaks whose position and width
are known (for example from the resolution model): a free shape biases the area of a
marginal peak upwards. The uncertainty of the fixed values is not included in the
result, and such widths are not offered for a resolution analysis.

## Synthetic data

Generate the standard scenarios with

```powershell
.venv\Scripts\python.exe scripts\generate_synthetic.py
```

They are written to `data\synthetic\hpge` and `data\synthetic\nai`, each file containing
the counts and the full ground truth. Synthetic spectra are labelled as such everywhere
and can only be combined with other synthetic spectra.
