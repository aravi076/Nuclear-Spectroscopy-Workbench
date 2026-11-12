"""Nuclear Data Explorer service: browsing, overlay gate and positions, peak matching."""

from __future__ import annotations

import pytest

from nsw.core.errors import ProcessingError
from nsw.services import nuclear_explorer as nx
from nsw.simulation import sources
from nsw.simulation.reference import fictitious_library
from tests.conftest import make_raw
from tests.unit.test_identification_service import spectrum_and_fits


@pytest.fixture(scope="module")
def syn_x():  # type: ignore[no-untyped-def]
    return spectrum_and_fits(sources.SYN_X)


def test_browsing_tables_show_provenance_and_conventions() -> None:
    lib = fictitious_library()
    headers, rows = nx.nuclide_table(lib)
    assert headers[0] == "Nuclide" and {r[0] for r in rows} >= {"SYN-X", "DECOY-A"}
    record = lib.records_for("SYN-X")[0]
    _, lines = nx.line_table(record)
    assert [r[0].split()[0] for r in lines] == ["200.000", "650.000", "1250.000"]
    assert all(r[1].endswith("/decay") for r in lines)
    _, strong = nx.line_table(record, min_fraction=0.7)
    assert len(strong) == 1
    _, ranged = nx.line_table(record, energy_range_kev=(500.0, 1300.0))
    assert len(ranged) == 2
    prov = dict(nx.provenance_rows(record))
    assert prov["Evaluation"] == "FICTITIOUS" and "Citation" in prov
    assert "fictitious test library" in nx.library_label(lib)
    assert nx.conflict_table(lib)[1] == []  # one evaluation per nuclide


def test_overlay_is_refused_without_verified_calibration() -> None:
    measured = make_raw([1, 2, 3, 4, 5])
    missing = nx.overlay_missing(measured, fictitious_library())
    assert any("verified energy calibration" in m for m in missing)
    with pytest.raises(ProcessingError, match="cannot be drawn"):
        nx.overlay_lines(measured, fictitious_library(), ["SYN-X"])


def test_overlay_positions_match_the_calibration(syn_x) -> None:  # type: ignore[no-untyped-def]
    spectrum, _ = syn_x
    lines = nx.overlay_lines(spectrum, fictitious_library(), ["SYN-X"])
    assert sorted(round(line.energy_kev) for line in lines) == [200, 650, 1250]
    cal = spectrum.energy_calibration
    for line in lines:
        assert float(cal.energy_at(line.channel)) == pytest.approx(line.energy_kev, abs=1e-3)
        assert 0.0 < line.relative_strength <= 1.0
    assert nx.channels_for_energies(spectrum, [1e7]) == [None]


def test_match_peak_ranks_the_true_nuclide(syn_x) -> None:  # type: ignore[no-untyped-def]
    spectrum, fits = syn_x
    cal = spectrum.energy_calibration
    target = next(
        f.peaks[0].centroid
        for f in fits
        if abs(float(cal.energy_at(f.peaks[0].centroid)) - 650) < 5
    )
    peak, matches = nx.match_peak(spectrum, fits, target, fictitious_library())
    assert abs(peak.energy_kev - 650.0) < 1.0
    assert matches and matches[0].nuclide == "SYN-X"
    assert matches[0].other_found == matches[0].other_strong
    rows = nx.match_rows(matches)
    assert rows[0][0] == "SYN-X" and rows[0][6] == "FICTITIOUS"
    with pytest.raises(ProcessingError, match="fit the peak first"):
        nx.match_peak(spectrum, [], target, fictitious_library())
