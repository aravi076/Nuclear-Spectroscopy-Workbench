"""Synthetic radioactive sources and a synthetic efficiency curve, for testing the
efficiency and activity calculations against known truth.

Everything here is an **arbitrary test value**: the "nuclides" SYN-CAL and SYN-X do not
exist, their energies, emission probabilities and half-lives are round numbers chosen
to exercise the software, and the efficiency model describes no real detector
(TECHNICAL_DECISIONS TD-018). Nothing in this module is nuclear reference data.

Model
-----
Expected full-energy-peak count rate per live second of a line of energy E:

    rate = eps(E) * P_gamma * A_0 * D(t_start - t_ref) * f(t_real)

with ln eps(E) = sum_k a_k ln(E / E_0)^k, D and f as in ``nsw.analysis.decay``
(re-implemented here from the same definitions, because the simulation layer does not
import the analysis layer; the validation tests compare the two).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from nsw.simulation import presets
from nsw.simulation.generator import SyntheticLine, SyntheticSpec

LN2 = math.log(2.0)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SyntheticEfficiency(_Model):
    """ln eps(E) = sum_k coefficients[k] * ln(E / reference_energy_kev)**k."""

    coefficients: tuple[float, ...] = Field(min_length=1)
    reference_energy_kev: float = Field(gt=0.0)

    def efficiency(self, energy_kev: float) -> float:
        log = math.log(energy_kev / self.reference_energy_kev)
        return math.exp(sum(a * log**k for k, a in enumerate(self.coefficients)))


class SyntheticNuclideLine(_Model):
    energy_kev: float = Field(gt=0.0)
    emission_probability: float = Field(gt=0.0, le=1.0)


class SyntheticSource(_Model):
    name: str = Field(min_length=1)
    activity_bq: float = Field(gt=0.0)  # true activity at ``reference_time``
    reference_time: datetime
    half_life_s: float = Field(gt=0.0)
    lines: tuple[SyntheticNuclideLine, ...] = Field(min_length=1)

    def activity_at(self, time: datetime) -> float:
        dt = (time - self.reference_time).total_seconds()
        return self.activity_bq * math.exp(-LN2 / self.half_life_s * dt)

    def in_measurement_factor(self, real_time_s: float) -> float:
        x = LN2 / self.half_life_s * real_time_s
        return -math.expm1(-x) / x


def line_rates(
    source: SyntheticSource,
    efficiency: SyntheticEfficiency,
    start_time: datetime,
    real_time_s: float,
    continuum_ratio: float = 0.0,
) -> tuple[SyntheticLine, ...]:
    """Generator lines (expected peak counts per live second) for one measurement."""
    mean_activity = source.activity_at(start_time) * source.in_measurement_factor(real_time_s)
    return tuple(
        SyntheticLine(
            energy_kev=line.energy_kev,
            rate_cps=efficiency.efficiency(line.energy_kev)
            * line.emission_probability
            * mean_activity,
            continuum_ratio=continuum_ratio,
            label=f"{source.name}-{line.energy_kev:g}keV",
        )
        for line in source.lines
    )


# ---------------------------------------------------------------------- presets
REFERENCE_TIME = datetime(2026, 1, 1, 12, 0, 0)
DAY_S = 86400.0

#: eps(500 keV) = exp(-5) = 0.67 %, falling roughly as E^-0.8 with slight curvature
SYNTHETIC_EFFICIENCY = SyntheticEfficiency(
    coefficients=(-5.0, -0.8, -0.1), reference_energy_kev=500.0
)

#: calibration source: eight lines from 80 to 1800 keV, half-life 2 years
SYN_CAL = SyntheticSource(
    name="SYN-CAL",
    activity_bq=20000.0,
    reference_time=REFERENCE_TIME,
    half_life_s=730.0 * DAY_S,
    lines=tuple(
        SyntheticNuclideLine(energy_kev=e, emission_probability=p)
        for e, p in (
            (80.0, 0.30),
            (150.0, 0.25),
            (300.0, 0.40),
            (500.0, 0.20),
            (800.0, 0.35),
            (1100.0, 0.15),
            (1400.0, 0.25),
            (1800.0, 0.10),
        )
    ),
)

#: "unknown" sample: three lines, half-life 30 days (decay matters over weeks)
SYN_X = SyntheticSource(
    name="SYN-X",
    activity_bq=5000.0,
    reference_time=REFERENCE_TIME,
    half_life_s=30.0 * DAY_S,
    lines=(
        SyntheticNuclideLine(energy_kev=200.0, emission_probability=0.50),
        SyntheticNuclideLine(energy_kev=650.0, emission_probability=0.30),
        SyntheticNuclideLine(energy_kev=1250.0, emission_probability=0.20),
    ),
)


# ---------------------------------------------------------------------- spectra
#: days between the source reference time and the start of each fictitious measurement
SOURCE_SCENARIO_DAYS: dict[str, float] = {"syn-cal-source": 40.0, "syn-x-sample": 10.0}


def source_scenarios() -> tuple[SyntheticSpec, ...]:
    """Spectra of the fictitious sources for the efficiency and activity pages.

    The measurement of SYN-CAL starts 40 days, that of SYN-X 10 days after the
    reference time of the sources (``REFERENCE_TIME``); synthetic spectra carry no start
    time, so the pages ask for this offset.
    """
    out = []
    for name, source, live, seed in (
        ("syn-cal-source", SYN_CAL, 20000.0, 3001),
        ("syn-x-sample", SYN_X, 5000.0, 3002),
    ):
        days = SOURCE_SCENARIO_DAYS[name]
        start = REFERENCE_TIME + timedelta(days=days)
        out.append(
            SyntheticSpec(
                synthetic_id=name,
                live_time_s=live,
                real_time_s=live,
                seed=seed,
                lines=line_rates(source, SYNTHETIC_EFFICIENCY, start, live, continuum_ratio=1.0),
                background=presets.HPGE_BACKGROUND,
                description=(
                    f"Fictitious source {source.name} measured {days:g} days after its "
                    "reference time; arbitrary test values, not nuclear data."
                ),
                **presets.HPGE_BASE,
            )
        )
    return tuple(out)
