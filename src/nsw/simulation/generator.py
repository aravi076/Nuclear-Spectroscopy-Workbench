"""Synthetic pulse-height spectra with exact, machine-readable ground truth.

Purpose: testing numerical recovery of *known* parameters. The detector response here
is a deliberately simple mathematical model. It does not represent any real detector,
and agreement with it is software verification, never experimental validation.

Model (all assumptions are also written into every ground-truth record)
----------------------------------------------------------------------
* Energy scale: E(ch) = c0 + c1 * ch, channels 1-based; channel ``ch`` collects the
  energy interval [E(ch - 0.5), E(ch + 0.5)).
* Full-energy peak: Gaussian in energy, sigma = FWHM(E0) / (2 sqrt(2 ln 2)), with
  FWHM(E) = sqrt(a + b E + c E^2). The expectation per channel is the exact integral of
  the Gaussian over the channel (difference of normal CDFs), not the density at the
  channel centre.
* Continuum per line: a *schematic* flat shelf from 0 up to the Compton edge
  E_C = E0 * 2k / (1 + 2k), k = E0 / (m_e c^2), whose upper edge is smeared with the
  detector resolution. Its total expectation inside the spectrum is
  ``continuum_ratio`` times the peak area. This is not a Klein-Nishina shape and has no
  multiple-scatter, escape, backscatter or summing features.
* Background: rate per channel = constant + amplitude * exp(-E / decay), in counts per
  second of live time.
* Counting statistics: each component is sampled independently from a Poisson
  distribution with NumPy's ``Generator(PCG64(seed))``; the spectrum is their sum.
* No dead-time, pile-up or gain-drift effects: ``real_time_s`` is only a label.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.special import ndtr

from nsw.core.calibration import CalibrationOrigin, CalibrationStatus, EnergyCalibration
from nsw.core.spectrum import DataOrigin, Spectrum, SpectrumKind
from nsw.detectors.profiles import DetectorType

GENERATOR_VERSION = "1"
FORMAT_TAG = "nsw-synthetic-spectrum"  # must match nsw.io.nsw_synthetic.FORMAT_TAG
FORMAT_VERSION = 1
# Electron rest energy in keV (CODATA 2018 recommended value 0.510 998 950 00 MeV).
ELECTRON_REST_ENERGY_KEV = 510.99895
FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))  # 2.3548...

MODEL_ASSUMPTIONS: tuple[str, ...] = (
    "Synthetic data: simplified mathematical detector model, not a real detector.",
    "Linear energy scale; channel ch integrates E(ch-0.5)..E(ch+0.5).",
    "Gaussian full-energy peaks integrated exactly over each channel.",
    "FWHM(E) = sqrt(a + b*E + c*E^2) with assumed, not measured, parameters.",
    "Schematic flat Compton shelf up to the Compton edge; no Klein-Nishina shape, "
    "escape peaks, backscatter peak or coincidence summing.",
    "Background rate = constant + amplitude*exp(-E/decay) per channel.",
    "Independent Poisson sampling per component; no dead time, pile-up or gain drift.",
    "Line energies are arbitrary test values, not nuclear reference data.",
)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ResolutionModel(_Model):
    """FWHM(E)[keV] = sqrt(a + b E + c E^2), E in keV."""

    a: float = Field(ge=0.0)
    b: float = Field(ge=0.0)
    c: float = Field(default=0.0, ge=0.0)

    def fwhm(self, energy_kev: float) -> float:
        v = self.a + self.b * energy_kev + self.c * energy_kev**2
        if v <= 0.0:
            raise ValueError(f"resolution model gives non-positive FWHM^2 at {energy_kev} keV")
        return math.sqrt(v)

    def sigma(self, energy_kev: float) -> float:
        return self.fwhm(energy_kev) / FWHM_PER_SIGMA


class SyntheticLine(_Model):
    energy_kev: float = Field(gt=0.0)
    rate_cps: float = Field(ge=0.0)  # expected full-energy-peak counts per live second
    continuum_ratio: float = Field(default=0.0, ge=0.0)  # continuum counts / peak counts
    label: str = ""


class BackgroundModel(_Model):
    constant_cps_per_channel: float = Field(default=0.0, ge=0.0)
    exp_amplitude_cps_per_channel: float = Field(default=0.0, ge=0.0)
    exp_decay_kev: float = Field(default=300.0, gt=0.0)


class SyntheticSpec(_Model):
    synthetic_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9._-]+$")
    detector_type: DetectorType
    detector_id: str = Field(min_length=1)
    n_channels: int = Field(gt=0, le=65536)
    energy_offset_kev: float
    energy_gain_kev_per_channel: float = Field(gt=0.0)
    resolution: ResolutionModel
    lines: tuple[SyntheticLine, ...] = ()
    background: BackgroundModel = BackgroundModel()
    live_time_s: float = Field(gt=0.0)
    real_time_s: float = Field(gt=0.0)
    seed: int = Field(ge=0)
    role: Literal["source", "background"] = "source"
    description: str = ""

    @model_validator(mode="after")
    def _check(self) -> Any:
        if self.live_time_s > self.real_time_s:
            raise ValueError("live time cannot exceed real time")
        if self.role == "background" and self.lines:
            raise ValueError("a background-role spectrum cannot contain source lines")
        return self

    def channel_of(self, energy_kev: float) -> float:
        """Continuous 1-based channel coordinate of an energy."""
        return (energy_kev - self.energy_offset_kev) / self.energy_gain_kev_per_channel


@dataclass(frozen=True)
class SyntheticResult:
    spec: SyntheticSpec
    spectrum: Spectrum
    expected: NDArray[np.float64]  # noise-free expectation per channel
    truth: dict[str, Any]

    def document(self) -> dict[str, Any]:
        # "format" first so that the type can be recognised from the head of the file;
        # the long channel array last.
        return {
            "format": FORMAT_TAG,
            "version": FORMAT_VERSION,
            "synthetic_id": self.spec.synthetic_id,
            "detector_id": self.spec.detector_id,
            "live_time_s": self.spec.live_time_s,
            "real_time_s": self.spec.real_time_s,
            "truth": self.truth,
            "counts": [int(c) for c in self.spectrum.counts],
        }

    def to_json(self) -> str:
        doc = self.document()
        counts = doc.pop("counts")
        head = json.dumps(doc, indent=1)
        body = head.rstrip().removesuffix("}").rstrip()
        return body + ',\n "counts": ' + json.dumps(counts) + "\n}\n"


# ---------------------------------------------------------------------- expectations
def channel_edges_kev(spec: SyntheticSpec) -> NDArray[np.float64]:
    """Energies of the n+1 channel boundaries (channel ch spans edges[ch-1], edges[ch])."""
    ch = np.arange(0.5, spec.n_channels + 1.0, 1.0)
    return spec.energy_offset_kev + spec.energy_gain_kev_per_channel * ch


def peak_expectation(spec: SyntheticSpec, line: SyntheticLine) -> NDArray[np.float64]:
    """Expected full-energy-peak counts per channel (exact Gaussian bin integrals)."""
    sigma = spec.resolution.sigma(line.energy_kev)
    cdf = ndtr((channel_edges_kev(spec) - line.energy_kev) / sigma)
    return np.asarray(line.rate_cps * spec.live_time_s * np.diff(cdf), dtype=np.float64)


def compton_edge_kev(energy_kev: float) -> float:
    k = energy_kev / ELECTRON_REST_ENERGY_KEV
    return energy_kev * 2.0 * k / (1.0 + 2.0 * k)


def continuum_expectation(spec: SyntheticSpec, line: SyntheticLine) -> NDArray[np.float64]:
    """Expected schematic-continuum counts per channel, normalised inside the spectrum."""
    out = np.zeros(spec.n_channels, dtype=np.float64)
    total = line.rate_cps * spec.live_time_s * line.continuum_ratio
    if total == 0.0:
        return out
    edge = compton_edge_kev(line.energy_kev)
    sigma = spec.resolution.sigma(edge)
    e = np.clip(channel_edges_kev(spec), 0.0, None)  # no counts below zero energy
    u = (e - edge) / sigma
    # antiderivative of the smeared step 1 - Phi(u):  E - sigma * (u*Phi(u) + phi(u))
    antider = e - sigma * (u * ndtr(u) + np.exp(-0.5 * u * u) / math.sqrt(2.0 * math.pi))
    shape = np.clip(np.diff(antider), 0.0, None)
    norm = float(shape.sum())
    if norm <= 0.0:
        return out
    return np.asarray(total * shape / norm, dtype=np.float64)


def background_expectation(spec: SyntheticSpec) -> NDArray[np.float64]:
    bg = spec.background
    centres = spec.energy_offset_kev + spec.energy_gain_kev_per_channel * np.arange(
        1, spec.n_channels + 1, dtype=np.float64
    )
    rate = bg.constant_cps_per_channel + bg.exp_amplitude_cps_per_channel * np.exp(
        -np.clip(centres, 0.0, None) / bg.exp_decay_kev
    )
    return np.asarray(rate * spec.live_time_s, dtype=np.float64)


# ---------------------------------------------------------------------- generation
def generate(spec: SyntheticSpec) -> SyntheticResult:
    """Sample one synthetic spectrum. Deterministic for a given spec (including seed)."""
    rng = np.random.Generator(np.random.PCG64(spec.seed))
    counts = np.zeros(spec.n_channels, dtype=np.int64)
    expected = np.zeros(spec.n_channels, dtype=np.float64)
    line_truth: list[dict[str, Any]] = []
    # Fixed sampling order (peak then continuum for each line, background last) is part
    # of the reproducibility contract.
    for line in spec.lines:
        mu_peak = peak_expectation(spec, line)
        mu_cont = continuum_expectation(spec, line)
        n_peak = rng.poisson(mu_peak)
        n_cont = rng.poisson(mu_cont)
        counts += n_peak + n_cont
        expected += mu_peak + mu_cont
        fwhm = spec.resolution.fwhm(line.energy_kev)
        line_truth.append(
            {
                "label": line.label,
                "energy_kev": line.energy_kev,
                "rate_cps": line.rate_cps,
                "centroid_channel": spec.channel_of(line.energy_kev),
                "fwhm_kev": fwhm,
                "fwhm_channels": fwhm / spec.energy_gain_kev_per_channel,
                "sigma_kev": fwhm / FWHM_PER_SIGMA,
                "expected_peak_counts": line.rate_cps * spec.live_time_s,
                "expected_peak_counts_in_range": float(mu_peak.sum()),
                "realised_peak_counts": int(n_peak.sum()),
                "compton_edge_kev": compton_edge_kev(line.energy_kev),
                "expected_continuum_counts": float(mu_cont.sum()),
                "realised_continuum_counts": int(n_cont.sum()),
            }
        )
    mu_bg = background_expectation(spec)
    n_bg = rng.poisson(mu_bg)
    counts += n_bg
    expected += mu_bg

    calibration = EnergyCalibration(
        coefficients=(spec.energy_offset_kev, spec.energy_gain_kev_per_channel),
        origin=CalibrationOrigin.SYNTHETIC_TRUTH,
        status=CalibrationStatus.VERIFIED,
        method="generator ground truth",
        detector_id=spec.detector_id,
    )
    spectrum = Spectrum(
        counts=counts,
        kind=SpectrumKind.BACKGROUND if spec.role == "background" else SpectrumKind.RAW,
        origin=DataOrigin.SYNTHETIC,
        live_time_s=spec.live_time_s,
        real_time_s=spec.real_time_s,
        detector_id=spec.detector_id,
        sample_name=spec.synthetic_id,
        sample_description=spec.description or None,
        energy_calibration=calibration,
        synthetic_id=spec.synthetic_id,
        metadata={
            "synthetic": "true",
            "detector_type": spec.detector_type.value,
            "seed": str(spec.seed),
            "generator_version": GENERATOR_VERSION,
        },
        warnings=("SYNTHETIC DATA - not a measurement",),
    )
    truth: dict[str, Any] = {
        "role": spec.role,
        "description": spec.description,
        "detector_type": spec.detector_type.value,
        "seed": spec.seed,
        "generator_version": GENERATOR_VERSION,
        "numpy_version": np.__version__,
        "bit_generator": "PCG64",
        "n_channels": spec.n_channels,
        "energy_calibration_kev": [spec.energy_offset_kev, spec.energy_gain_kev_per_channel],
        "resolution_fwhm2_kev2": spec.resolution.model_dump(),
        "lines": line_truth,
        "background": {
            **spec.background.model_dump(),
            "expected_counts": float(mu_bg.sum()),
            "realised_counts": int(n_bg.sum()),
        },
        "total_expected_counts": float(expected.sum()),
        "total_counts": int(counts.sum()),
        "data_sha256": spectrum.data_sha256,
        "spec_sha256": hashlib.sha256(spec.model_dump_json().encode()).hexdigest(),
        "assumptions": list(MODEL_ASSUMPTIONS),
    }
    return SyntheticResult(spec, spectrum, expected, truth)


def write_synthetic(result: SyntheticResult, directory: Path) -> Path:
    """Write ``<synthetic_id>.nswsyn.json``. Never overwrites a file with other content."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result.spec.synthetic_id}.nswsyn.json"
    text = result.to_json()
    if path.exists():
        if path.read_text(encoding="utf-8") == text:
            return path
        raise FileExistsError(f"{path} exists with different content; refusing to overwrite")
    with path.open("x", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path
