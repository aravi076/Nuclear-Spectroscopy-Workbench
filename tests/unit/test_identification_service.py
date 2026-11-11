"""The identification service: calibration gate, library rules, full synthetic chain.

All nuclides are fictitious (``nsw.simulation.reference``).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from nsw.analysis.fitting import FitResult, fit_peaks, fit_region_for
from nsw.core.calibration import CalibrationStatus
from nsw.core.nuclear_data import (
    GammaRecord,
    IntensityConvention,
    NuclideRecord,
    Provenance,
    ReferenceLibrary,
    ReuseStatus,
)
from nsw.core.spectrum import Spectrum
from nsw.io.reference_data import write_library
from nsw.services import identification as ids
from nsw.simulation import presets, sources
from nsw.simulation.generator import SyntheticSpec, generate
from tests.conftest import make_raw

MODEL = sources.SYNTHETIC_EFFICIENCY
T0 = sources.REFERENCE_TIME


def spectrum_and_fits(
    *srcs: sources.SyntheticSource, live: float = 5000.0, seed: int = 7
) -> tuple[Spectrum, list[FitResult]]:
    lines = tuple(
        ln for s in srcs for ln in sources.line_rates(s, MODEL, T0, live, continuum_ratio=1.0)
    )
    spec = SyntheticSpec(
        synthetic_id=f"ident-{seed}",
        live_time_s=live,
        real_time_s=live,
        seed=seed,
        lines=lines,
        background=presets.HPGE_BACKGROUND,
        **presets.HPGE_BASE,
    )
    res = generate(spec)
    fits = []
    for line in res.truth["lines"]:
        c, w = line["centroid_channel"], line["fwhm_channels"]
        first, last = fit_region_for(res.spectrum, [c], w)
        fits.append(fit_peaks(res.spectrum, first, last, [c], w))
    return res.spectrum, fits


def real_like_library() -> ReferenceLibrary:
    """An invented record labelled as user-supplied data (element 'Xx' does not exist)."""
    prov = Provenance(
        evaluation="DDEP",
        dataset_id="invented",
        citation="invented test record, not nuclear data",
        licence="none (test)",
        reuse_status=ReuseStatus.USER_SUPPLIED,
        retrieval_date=date(2026, 11, 11),
        parser="test/1",
    )
    line = GammaRecord(energy_kev=650.0, intensity=0.5, convention=IntensityConvention.PER_DECAY)
    return ReferenceLibrary(
        name="invented", records=(NuclideRecord(nuclide="Xx-999", provenance=prov, lines=(line,)),)
    )


def test_spectra_without_verified_calibration_are_refused() -> None:
    lib = ids.fictitious_library()
    experimental = make_raw(np.full(200, 10))
    assert experimental.calibration_status is not CalibrationStatus.VERIFIED
    missing = ids.missing_inputs(experimental, real_like_library())
    assert len(missing) == 1 and "verified energy calibration" in missing[0]
    assert "file or folder names cannot identify" in missing[0]
    with pytest.raises(ids.ProcessingError, match="verified energy calibration"):
        ids.identify(experimental, [], real_like_library())
    with pytest.raises(ids.ProcessingError, match="verified energy calibration"):
        ids.observed_peaks(experimental, [])
    # the fictitious library is never applied to a measurement
    assert any("fictitious test library" in m for m in ids.missing_inputs(experimental, lib))
    assert any("NSW contains no nuclear data" in m for m in ids.missing_inputs(experimental, None))
    # a synthetic spectrum that lost its calibration is refused as well
    spectrum, fits = spectrum_and_fits(sources.SYN_X)
    bare = spectrum.evolve(energy_calibration=None)
    with pytest.raises(ids.ProcessingError, match="verified energy calibration"):
        ids.identify(bare, fits, lib)
    # and synthetic spectra are not compared with (what is labelled as) real data
    with pytest.raises(ids.ProcessingError, match="not compared with real nuclear data"):
        ids.identify(spectrum, fits, real_like_library())
    assert ids.result_label(experimental).startswith("EXPERIMENTAL — candidates only")


def test_full_chain_on_a_synthetic_spectrum() -> None:
    spectrum, fits = spectrum_and_fits(sources.SYN_X)
    assert all(f.ok for f in fits)
    lib = ids.fictitious_library()
    peaks = ids.observed_peaks(spectrum, fits)
    assert [round(p.energy_kev) for p in peaks] == [200, 650, 1250]
    gain = presets.HPGE_BASE["energy_gain_kev_per_channel"]
    assert peaks[0].position_unc_kev == pytest.approx(gain * fits[0].peaks[0].centroid_unc)
    assert peaks[0].calibration_unc_kev == 0.0  # exact synthetic truth has no covariance
    assert peaks[0].area_unc == fits[0].peaks[0].area_unc

    # with the statistical position uncertainty alone DECOY-A (0.05 keV off a strong
    # peak) drops out; DECOY-B (0.03 keV off the weakest peak) stays inside the window
    strict = ids.identify(spectrum, fits, lib)
    assert [c.nuclide for c in strict.candidates] == ["SYN-X", "DECOY-B"]
    assert strict.candidates[0].confidence is ids.Confidence.PROBABLE
    assert strict.energy_range_kev == pytest.approx((0.75, 0.5 + 0.25 * 8192))
    assert all(abs(m.z) < 3 for m in strict.candidates[0].matches)

    # an allowance of 0.05 keV brings the coincidences in; the ranking must hold
    loose = ids.identify(spectrum, fits, lib, ids.IdentificationSettings(extra_energy_unc_kev=0.05))
    by_name = {c.nuclide: c for c in loose.candidates}
    assert loose.candidates[0].nuclide == "SYN-X"
    assert by_name["DECOY-A"].confidence is ids.Confidence.NOT_SUPPORTED
    assert by_name["DECOY-B"].confidence is ids.Confidence.POSSIBLE
    assert loose.settings.extra_energy_unc_kev == 0.05 and loose.settings.k == 3.0

    headers, rows = ids.candidate_table(loose)
    assert headers[0] == "Nuclide" and rows[0][0] == "SYN-X" and "FICTITIOUS" in rows[0][7]
    evidence = ids.evidence_rows(spectrum, loose, by_name["DECOY-A"])
    assert evidence[0] == ("Status", "SYNTHETIC — fictitious nuclides; software verification only")
    assert any(k.startswith("MISSING 950.00") for k, _ in evidence)
    assert any("shared with SYN-X" in v for _, v in evidence)
    assert any("not identifications" in v for k, v in evidence if k == "Caution")
    text = ids.identification_csv_text(spectrum, loose)
    assert "# status: SYNTHETIC" in text and "k = 3.0" in text
    assert "DECOY-A,not supported" in text and ",missing," in text


def test_missing_line_is_judged_against_the_counts_in_the_spectrum() -> None:
    # SYN-CAL measured, but its 1800 keV peak is not offered to the identification
    spectrum, fits = spectrum_and_fits(sources.SYN_CAL, live=20000.0, seed=11)
    lib = ids.fictitious_library()
    from tests.validation.test_identification import curve

    without = [f for f in fits if abs(f.peaks[0].centroid - (1800.0 - 0.5) / 0.25) > 5]
    result = ids.identify(spectrum, without, lib, efficiency=curve())
    syn_cal = next(c for c in result.candidates if c.nuclide == "SYN-CAL")
    (missing,) = syn_cal.missing
    assert missing.line.energy_kev == 1800.0
    assert missing.predicted_area > 2 * missing.detectable_area > 0
    assert syn_cal.confidence is ids.Confidence.POSSIBLE
    full = ids.identify(spectrum, fits, lib, efficiency=curve())
    assert full.candidates[0].nuclide == "SYN-CAL"
    assert full.candidates[0].confidence is ids.Confidence.CONSISTENT


def test_library_file_tables_and_export(tmp_path: Path) -> None:
    path = write_library(ids.fictitious_library(), tmp_path / "fictitious.json")
    lib = ids.load_reference_file(path)
    assert lib.name == "fictitious" and lib.fictitious
    assert [r.nuclide for r in ids.select_records(lib)][:2] == ["DECOY-A", "DECOY-B"]
    headers, rows = ids.library_table(lib)
    assert "Reuse" in headers and all(r[6] == "fictitious" for r in rows)
    assert any(r[4].startswith("relative") for r in rows)
    headers, rows = ids.sources_table()
    assert [r[0] for r in rows] == ["DDEP", "ENSDF"]
    # a library without a record of the preferred evaluations
    spectrum, fits = spectrum_and_fits(sources.SYN_X)
    with pytest.raises(ids.ProcessingError, match="no record from the selected evaluations"):
        ids.identify(spectrum, fits, lib, preference=("DDEP",))
    result = ids.identify(spectrum, fits, lib)
    out = ids.export_identification_csv(spectrum, result, tmp_path / "ident.csv")
    assert out.read_text(encoding="utf-8").startswith("# NSW nuclide candidates")
    with pytest.raises(ids.ExportError):
        ids.export_identification_csv(spectrum, result, out)
