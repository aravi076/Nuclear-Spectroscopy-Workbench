"""Quantity and unit tests. Expected values are derived by hand in the comments."""

from __future__ import annotations

import math

import pytest

from nsw.core import Quantity, Unit, UnitError
from nsw.core.units import (
    add_independent,
    conversion_factor,
    dimension_of,
    ratio_independent,
    sub_independent,
)


def test_conversion_between_energy_units() -> None:
    q = Quantity(1.3325, Unit.MEV, stat=0.0002, sys=0.0001).to(Unit.KEV)
    assert q.unit is Unit.KEV
    assert q.value == pytest.approx(1332.5)
    assert q.stat == pytest.approx(0.2)
    assert q.sys == pytest.approx(0.1)


def test_incompatible_units_rejected() -> None:
    with pytest.raises(UnitError):
        Quantity(1.0, Unit.KEV).to(Unit.SECOND)
    with pytest.raises(UnitError):
        add_independent(Quantity(1.0, Unit.KEV), Quantity(1.0, Unit.COUNT))
    with pytest.raises(UnitError):
        Quantity(1.0, "keV")  # type: ignore[arg-type]


def test_every_unit_has_a_dimension() -> None:
    for unit in Unit:
        assert dimension_of(unit)
    assert conversion_factor(Unit.KILOBECQUEREL, Unit.BECQUEREL) == 1000.0
    assert conversion_factor(Unit.PERCENT, Unit.DIMENSIONLESS) == 0.01


def test_combined_uncertainty_is_quadrature_sum() -> None:
    q = Quantity(10.0, Unit.COUNT, stat=3.0, sys=4.0)
    assert q.uncertainty == pytest.approx(5.0)  # sqrt(9 + 16)
    assert q.relative_uncertainty == pytest.approx(0.5)


def test_poisson_count_uncertainty() -> None:
    q = Quantity.from_counts(400)
    assert q.stat == pytest.approx(20.0)  # sqrt(400)
    assert q.sys == 0.0
    assert Quantity.from_counts(0).stat == 0.0
    with pytest.raises(ValueError):
        Quantity.from_counts(-1)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_values_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        Quantity(bad, Unit.KEV)
    with pytest.raises(ValueError):
        Quantity(1.0, Unit.KEV, stat=bad)


def test_negative_uncertainty_rejected() -> None:
    with pytest.raises(ValueError):
        Quantity(1.0, Unit.KEV, sys=-0.1)


def test_add_and_subtract_independent() -> None:
    a = Quantity(100.0, Unit.COUNT, stat=10.0, sys=1.0)
    b = Quantity(64.0, Unit.COUNT, stat=8.0, sys=2.0)
    s = add_independent(a, b)
    d = sub_independent(a, b)
    assert s.value == 164.0 and d.value == 36.0
    # stat: sqrt(100 + 64); sys: sqrt(1 + 4); identical for sum and difference
    for r in (s, d):
        assert r.stat == pytest.approx(math.sqrt(164.0))
        assert r.sys == pytest.approx(math.sqrt(5.0))


def test_addition_converts_units() -> None:
    s = add_independent(Quantity(1.0, Unit.MEV), Quantity(500.0, Unit.KEV))
    assert s.unit is Unit.MEV and s.value == pytest.approx(1.5)


def test_ratio_first_order_propagation() -> None:
    # r = 200/100 = 2; relative stat = sqrt(0.05^2 + 0.03^2)
    r = ratio_independent(
        Quantity(200.0, Unit.COUNT, stat=10.0),
        Quantity(100.0, Unit.SECOND, stat=3.0),
        Unit.COUNT_PER_SECOND,
    )
    assert r.value == pytest.approx(2.0)
    assert r.stat / r.value == pytest.approx(math.hypot(0.05, 0.03))
    with pytest.raises(ZeroDivisionError):
        ratio_independent(
            Quantity(1.0, Unit.COUNT), Quantity(0.0, Unit.SECOND), Unit.COUNT_PER_SECOND
        )


def test_scaling_by_exact_constant() -> None:
    q = Quantity(10.0, Unit.COUNT, stat=2.0, sys=1.0).scaled(-0.5)
    assert (q.value, q.stat, q.sys) == (-5.0, 1.0, 0.5)


def test_formatting_matches_uncertainty_precision() -> None:
    assert Quantity(661.6573, Unit.KEV, stat=0.0031).format() == "661.6573 ± 0.0031 keV"
    assert Quantity(1234.5678, Unit.COUNT, stat=35.2).format() == "1235 ± 35 count"
    assert Quantity(12345.0, Unit.COUNT, stat=1234.0).format() == "12300 ± 1200 count"
    assert Quantity(5.0, Unit.SECOND).format() == "5 s"


def test_quantity_is_immutable() -> None:
    q = Quantity(1.0, Unit.KEV)
    with pytest.raises(AttributeError):
        q.value = 2.0  # type: ignore[misc]
