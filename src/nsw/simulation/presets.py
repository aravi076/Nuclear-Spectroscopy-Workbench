"""Controlled synthetic test scenarios for HPGe-like and NaI(Tl)-like detectors.

Every number below is an **assumed, illustrative** parameter chosen to exercise the
software. None describes the laboratory's detector, and the line energies are arbitrary
round test values, not nuclear data.

Assumed detector models
-----------------------
HPGe-like:  8192 channels, E = 0.5 + 0.25*ch keV (range to ~2048 keV),
            FWHM^2 = 0.75 + 0.0025*E  -> FWHM 1.00 keV at 100 keV, 2.00 keV at 1300 keV.
NaI-like:   1024 channels, E = 0.0 + 3.0*ch keV (range to ~3072 keV),
            FWHM^2 = 3.24*E           -> FWHM 46.3 keV (7.0 %) at 662 keV.
"""

from __future__ import annotations

from typing import Any

from nsw.detectors.profiles import DetectorType, EvidenceLevel, HPGeProfile, McaInfo, NaIProfile
from nsw.simulation.generator import (
    BackgroundModel,
    ResolutionModel,
    SyntheticLine,
    SyntheticSpec,
)

HPGE_ID = "synthetic-hpge"
NAI_ID = "synthetic-nai"

HPGE_BASE: dict[str, Any] = dict(
    detector_type=DetectorType.HPGE,
    detector_id=HPGE_ID,
    n_channels=8192,
    energy_offset_kev=0.5,
    energy_gain_kev_per_channel=0.25,
    resolution=ResolutionModel(a=0.75, b=0.0025),
)
NAI_BASE: dict[str, Any] = dict(
    detector_type=DetectorType.NAI_TL,
    detector_id=NAI_ID,
    n_channels=1024,
    energy_offset_kev=0.0,
    energy_gain_kev_per_channel=3.0,
    resolution=ResolutionModel(a=0.0, b=3.24),
)
HPGE_BACKGROUND = BackgroundModel(
    constant_cps_per_channel=0.0005, exp_amplitude_cps_per_channel=0.01, exp_decay_kev=300.0
)
NAI_BACKGROUND = BackgroundModel(
    constant_cps_per_channel=0.005, exp_amplitude_cps_per_channel=0.2, exp_decay_kev=300.0
)
_ISOLATED = (100.0, 300.0, 600.0, 1000.0, 1500.0)


def synthetic_profiles() -> tuple[HPGeProfile, NaIProfile]:
    note = "Simulated detector with assumed parameters; not a real instrument."
    return (
        HPGeProfile(
            detector_id=HPGE_ID,
            name="Synthetic HPGe-like detector",
            type_evidence=EvidenceLevel.SYNTHETIC,
            n_channels=8192,
            mca=McaInfo(conversion_gain_channels=8192),
            notes=note,
        ),
        NaIProfile(
            detector_id=NAI_ID,
            name="Synthetic NaI(Tl)-like detector",
            type_evidence=EvidenceLevel.SYNTHETIC,
            n_channels=1024,
            mca=McaInfo(conversion_gain_channels=1024),
            notes=note,
        ),
    )


def _lines(
    energies: tuple[float, ...], rate: float, ratio: float, prefix: str
) -> tuple[SyntheticLine, ...]:
    return tuple(
        SyntheticLine(
            energy_kev=e, rate_cps=rate, continuum_ratio=ratio, label=f"{prefix}-{e:g}keV"
        )
        for e in energies
    )


def _scenarios(
    tag: str,
    base: dict[str, Any],
    background: BackgroundModel,
    continuum_ratio: float,
    doublet: tuple[float, float],
    seed0: int,
) -> tuple[SyntheticSpec, ...]:
    def spec(name: str, seed: int, live: float, **kw: Any) -> SyntheticSpec:
        return SyntheticSpec(
            synthetic_id=f"{tag}-{name}",
            live_time_s=live,
            real_time_s=live * 1.02,
            seed=seed,
            **base,
            **kw,
        )

    return (
        spec(
            "isolated-peaks",
            seed0,
            1000.0,
            lines=_lines(_ISOLATED, 20.0, continuum_ratio, "test"),
            background=background,
            description="Five well-separated lines on continuum and background.",
        ),
        spec(
            "isolated-peaks-no-continuum",
            seed0 + 1,
            1000.0,
            lines=_lines(_ISOLATED, 20.0, 0.0, "test"),
            description="Five lines only: no continuum, no background (pure peak-shape test).",
        ),
        spec(
            "overlapping-doublet",
            seed0 + 2,
            1000.0,
            lines=(
                SyntheticLine(
                    energy_kev=doublet[0],
                    rate_cps=30.0,
                    continuum_ratio=continuum_ratio,
                    label="doublet-low",
                ),
                SyntheticLine(
                    energy_kev=doublet[1],
                    rate_cps=10.0,
                    continuum_ratio=continuum_ratio,
                    label="doublet-high",
                ),
            ),
            background=background,
            description="Two overlapping lines with a 3:1 area ratio.",
        ),
        spec(
            "low-count",
            seed0 + 3,
            60.0,
            lines=_lines(_ISOLATED, 0.5, continuum_ratio, "test"),
            background=background,
            description="About 30 expected counts per peak: Poisson regime.",
        ),
        spec(
            "high-count",
            seed0 + 4,
            10000.0,
            lines=_lines(_ISOLATED, 200.0, continuum_ratio, "test"),
            background=background,
            description="About 2e6 expected counts per peak.",
        ),
        spec(
            "background-only",
            seed0 + 5,
            5000.0,
            role="background",
            background=background,
            description="Independent background run matching the scenario background model.",
        ),
    )


def hpge_scenarios() -> tuple[SyntheticSpec, ...]:
    # doublet separated by 3 keV where FWHM is ~1.5 keV
    return _scenarios("hpge", HPGE_BASE, HPGE_BACKGROUND, 3.0, (600.0, 603.0), seed0=1000)


def nai_scenarios() -> tuple[SyntheticSpec, ...]:
    # doublet separated by 40 keV where FWHM is ~44 keV: unresolved by eye
    return _scenarios("nai", NAI_BASE, NAI_BACKGROUND, 1.5, (600.0, 640.0), seed0=2000)
