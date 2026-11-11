"""Explicit measurement units and value-with-uncertainty quantities.

Design notes
------------
* Every physical number that crosses a module boundary is either a NumPy array whose
  unit is fixed by the attribute name (``live_time_s``) or a :class:`Quantity`.
* Statistical (type A, counting) and systematic (type B) standard uncertainties are kept
  separately and are only combined, in quadrature, for presentation.
* The arithmetic helpers here assume **independent** operands. Correlated inputs (for
  example several results sharing one calibration) must go through covariance-aware
  propagation, which is a later phase; these helpers must not be used for them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from nsw.core.errors import UnitError


class Unit(StrEnum):
    DIMENSIONLESS = "1"
    PERCENT = "%"
    COUNT = "count"
    CHANNEL = "channel"
    SECOND = "s"
    MICROSECOND = "us"
    EV = "eV"
    KEV = "keV"
    MEV = "MeV"
    COUNT_PER_SECOND = "count/s"
    BECQUEREL = "Bq"
    KILOBECQUEREL = "kBq"
    KEV_PER_CHANNEL = "keV/channel"
    VOLT = "V"


# unit -> (dimension name, factor converting to the dimension's base unit)
_DIMENSIONS: dict[Unit, tuple[str, float]] = {
    Unit.DIMENSIONLESS: ("dimensionless", 1.0),
    Unit.PERCENT: ("dimensionless", 0.01),
    Unit.COUNT: ("count", 1.0),
    Unit.CHANNEL: ("channel", 1.0),
    Unit.SECOND: ("time", 1.0),
    Unit.MICROSECOND: ("time", 1.0e-6),
    Unit.EV: ("energy", 1.0e-3),
    Unit.KEV: ("energy", 1.0),
    Unit.MEV: ("energy", 1.0e3),
    Unit.COUNT_PER_SECOND: ("count rate", 1.0),
    Unit.BECQUEREL: ("activity", 1.0),
    Unit.KILOBECQUEREL: ("activity", 1.0e3),
    Unit.KEV_PER_CHANNEL: ("gain", 1.0),
    Unit.VOLT: ("voltage", 1.0),
}


def dimension_of(unit: Unit) -> str:
    return _DIMENSIONS[unit][0]


def conversion_factor(src: Unit, dst: Unit) -> float:
    """Factor ``f`` such that ``value_in_dst = f * value_in_src``."""
    src_dim, src_f = _DIMENSIONS[src]
    dst_dim, dst_f = _DIMENSIONS[dst]
    if src_dim != dst_dim:
        raise UnitError(f"cannot convert {src.value!r} ({src_dim}) to {dst.value!r} ({dst_dim})")
    return src_f / dst_f


@dataclass(frozen=True, slots=True)
class Quantity:
    """A value with unit and separate statistical / systematic standard uncertainties."""

    value: float
    unit: Unit
    stat: float = 0.0
    sys: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.unit, Unit):
            raise UnitError(f"unit must be a Unit, got {self.unit!r}")
        if not math.isfinite(self.value):
            raise ValueError(f"value must be finite, got {self.value!r}")
        for name, u in (("stat", self.stat), ("sys", self.sys)):
            if not math.isfinite(u) or u < 0.0:
                raise ValueError(f"{name} uncertainty must be finite and >= 0, got {u!r}")

    # -- uncertainty views -------------------------------------------------
    @property
    def uncertainty(self) -> float:
        """Combined standard uncertainty (quadrature sum of stat and sys)."""
        return math.hypot(self.stat, self.sys)

    @property
    def relative_uncertainty(self) -> float:
        if self.value == 0.0:
            raise ZeroDivisionError("relative uncertainty is undefined for a zero value")
        return self.uncertainty / abs(self.value)

    # -- exact transformations ---------------------------------------------
    def to(self, unit: Unit) -> Quantity:
        f = conversion_factor(self.unit, unit)
        return Quantity(self.value * f, unit, self.stat * f, self.sys * f)

    def scaled(self, factor: float, unit: Unit | None = None) -> Quantity:
        """Multiply by an exact constant (no uncertainty), optionally relabelling the unit."""
        if not math.isfinite(factor):
            raise ValueError("scale factor must be finite")
        a = abs(factor)
        return Quantity(self.value * factor, unit or self.unit, self.stat * a, self.sys * a)

    # -- construction helpers ----------------------------------------------
    @classmethod
    def from_counts(cls, n: float) -> Quantity:
        """Observed Poisson count with the usual sqrt(N) estimate of its standard deviation.

        For N = 0 this gives a zero uncertainty estimate, which understates the true
        uncertainty of the underlying expectation; callers working with very low counts
        must use a likelihood-based treatment instead.
        """
        if n < 0:
            raise ValueError("an observed count cannot be negative")
        return cls(float(n), Unit.COUNT, stat=math.sqrt(n))

    # -- presentation --------------------------------------------------------
    def format(self, sig: int = 2) -> str:
        """Value rounded to the precision of its uncertainty (``sig`` significant digits)."""
        u = self.uncertainty
        if u == 0.0:
            return f"{self.value:g} {self.unit.value}"
        exponent = math.floor(math.log10(u))
        decimals = max(0, -(exponent - (sig - 1)))
        quantum = 10.0 ** (exponent - (sig - 1))
        v = round(self.value / quantum) * quantum
        ur = round(u / quantum) * quantum
        return f"{v:.{decimals}f} ± {ur:.{decimals}f} {self.unit.value}"

    def __str__(self) -> str:
        return self.format()


def _same_dimension(a: Quantity, b: Quantity) -> Quantity:
    """Return ``b`` expressed in the unit of ``a`` (raises UnitError if incompatible)."""
    return b.to(a.unit)


def add_independent(a: Quantity, b: Quantity) -> Quantity:
    """a + b for independent quantities of the same dimension (result in unit of ``a``)."""
    b2 = _same_dimension(a, b)
    return Quantity(
        a.value + b2.value, a.unit, math.hypot(a.stat, b2.stat), math.hypot(a.sys, b2.sys)
    )


def sub_independent(a: Quantity, b: Quantity) -> Quantity:
    """a - b for independent quantities of the same dimension (result in unit of ``a``)."""
    b2 = _same_dimension(a, b)
    return Quantity(
        a.value - b2.value, a.unit, math.hypot(a.stat, b2.stat), math.hypot(a.sys, b2.sys)
    )


def ratio_independent(a: Quantity, b: Quantity, unit: Unit) -> Quantity:
    """a / b for independent quantities, first-order propagation; caller names the unit.

    First-order propagation is only adequate when the relative uncertainty of ``b`` is
    small (a few percent); this is the caller's responsibility.
    """
    if b.value == 0.0:
        raise ZeroDivisionError("division by a zero-valued quantity")
    r = a.value / b.value

    def comp(ua: float, ub: float) -> float:
        return math.hypot(ua / b.value, r * ub / b.value)

    return Quantity(r, unit, comp(a.stat, b.stat), comp(a.sys, b.sys))
