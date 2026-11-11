"""A fictitious reference library for testing nuclide identification.

**Nothing here is nuclear data.** The "nuclides" are the invented test sources of
``nsw.simulation.sources`` (SYN-CAL, SYN-X) and invented decoys whose lines are placed
to provoke the failure modes of an identification algorithm:

DECOY-A   one line coincident with SYN-X (650 keV) and a stronger line at 950 keV that
          no synthetic spectrum contains: a chance coincidence with a missing strong line
DECOY-B   a single line coincident with SYN-X (1250 keV): one line only
DECOY-C   two lines coincident with SYN-CAL (300, 800 keV) but with a very different
          intensity ratio: separable only with an efficiency curve
DECOY-D   two lines coincident with SYN-CAL (150, 500 keV) given as *relative*
          intensities without normalisation
DECOY-E   lines that coincide with nothing

Every record carries the provenance "FICTITIOUS" and the model refuses to mix such
records with real data.
"""

from __future__ import annotations

from datetime import date

from nsw.core.nuclear_data import (
    DecayBranch,
    GammaRecord,
    IntensityConvention,
    NuclideRecord,
    Provenance,
    ReferenceLibrary,
    ReuseStatus,
)
from nsw.simulation.sources import DAY_S, SYN_CAL, SYN_X, SyntheticSource

LINE_ENERGY_UNC_KEV = 0.01  # invented
REL_INTENSITY_UNC = 0.01  # invented
PARSER = "nsw.simulation.reference/1"

FICTITIOUS = Provenance(
    evaluation="FICTITIOUS",
    dataset_id="nsw-fictitious-library-1",
    citation="NSW test fixture: invented values, not nuclear data (TECHNICAL_DECISIONS TD-018)",
    licence="part of NSW; no nuclear data contained",
    reuse_status=ReuseStatus.FICTITIOUS,
    retrieval_date=date(2026, 11, 11),
    parser=PARSER,
)


def _line(energy: float, intensity: float, convention: IntensityConvention) -> GammaRecord:
    return GammaRecord(
        energy_kev=energy,
        energy_unc_kev=LINE_ENERGY_UNC_KEV,
        intensity=intensity,
        intensity_unc=REL_INTENSITY_UNC * intensity,
        convention=convention,
    )


def _record(
    name: str,
    half_life_s: float,
    lines: tuple[tuple[float, float], ...],
    convention: IntensityConvention = IntensityConvention.PER_DECAY,
    comment: str = "",
) -> NuclideRecord:
    return NuclideRecord(
        nuclide=name,
        provenance=FICTITIOUS,
        half_life_s=half_life_s,
        half_life_unc_s=0.0,
        half_life_as_stated=f"{half_life_s / DAY_S:g} d (invented)",
        branches=(DecayBranch(mode="fictitious", fraction=1.0),),
        lines=tuple(_line(e, p, convention) for e, p in lines),
        comment=comment or "invented test nuclide",
    )


def record_of(source: SyntheticSource) -> NuclideRecord:
    """Library record with exactly the lines the generator uses for ``source``."""
    return _record(
        source.name,
        source.half_life_s,
        tuple((line.energy_kev, line.emission_probability) for line in source.lines),
    )


DECOYS: tuple[NuclideRecord, ...] = (
    _record("DECOY-A", 100.0 * DAY_S, ((650.05, 0.60), (950.0, 0.80))),
    _record("DECOY-B", 10.0 * DAY_S, ((1250.03, 0.90),)),
    _record("DECOY-C", 500.0 * DAY_S, ((300.02, 0.10), (800.03, 0.80))),
    _record(
        "DECOY-D",
        50.0 * DAY_S,
        ((150.02, 100.0), (500.03, 40.0)),
        IntensityConvention.RELATIVE,
        "invented test nuclide with relative intensities (no normalisation)",
    ),
    _record("DECOY-E", 5.0 * DAY_S, ((432.0, 0.70), (977.0, 0.50))),
)


def fictitious_library() -> ReferenceLibrary:
    return ReferenceLibrary(
        name="NSW fictitious test library (not nuclear data)",
        records=(record_of(SYN_CAL), record_of(SYN_X), *DECOYS),
    )
