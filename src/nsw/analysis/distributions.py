"""Probability functions used by the goodness-of-fit tests.

These are the same quantities as ``scipy.stats.chi2.sf``, ``scipy.stats.norm.sf`` and
``scipy.stats.poisson.pmf``, computed with ``scipy.special`` directly. ``scipy.stats``
takes about 2.5 s to import and was the second-largest part of the application's start
time (recommendation R1); ``scipy.special`` loads in milliseconds. The equivalence is
tested against ``scipy.stats`` over argument grids including the far tails
(``tests/unit/test_distributions.py``).

- chi-square survival function: Q(k/2, x/2) (regularised upper incomplete gamma),
  ``scipy.special.chdtrc``; ``scipy.stats.chi2.sf`` calls the same routine.
- normal survival function: Φ(−z), ``scipy.special.ndtr``, accurate in the upper tail
  because Φ is evaluated at the negative argument.
- Poisson probability: exp(n·ln μ − μ − ln n!), with n·ln μ = 0 for n = 0 (``xlogy``).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.special import chdtrc, gammaln, ndtr, xlogy


def chi2_sf(x: float, dof: float) -> float:
    """P(X ≥ x) for X chi-square distributed with ``dof`` degrees of freedom."""
    if x < 0.0:
        return 1.0
    return float(chdtrc(dof, x))


def normal_sf(z: float) -> float:
    """P(Z ≥ z) for a standard normal Z."""
    return float(ndtr(-z))


def poisson_pmf(n: ArrayLike, mean: ArrayLike) -> NDArray[np.float64]:
    """P(N = n) for N Poisson distributed with the given mean (broadcasting)."""
    k = np.asarray(n, dtype=np.float64)
    mu = np.asarray(mean, dtype=np.float64)
    out: NDArray[np.float64] = np.exp(xlogy(k, mu) - mu - gammaln(k + 1.0))
    return out
