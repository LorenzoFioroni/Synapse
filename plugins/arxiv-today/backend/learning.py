"""Learning how much each kind of evidence counts for the user, from what they do with the feed.

Labels (``labels``):
- a paper added to the library on or after its announcement: wanted, strength 1;
- a paper opened (shown in the item pane for a few seconds, or its PDF opened): probably wanted,
  0.3;
- a paper dismissed: not wanted, 1;
- a paper *passed*: listed above a paper the user opened or added on that day, and not opened
  itself: not wanted, 0.3. Papers below the last one opened or added get no label (the user may
  not have reached them), and a day the user never opened gives none at all.

The fit (``fit``) is a logistic regression on the scoring features with the baseline held fixed,
so the tiers keep their meaning; only the weights move. Wanted and unwanted labels weigh the same
in total, whatever their counts, and an L2 prior pulls the weights toward the hand-set ones, so a
few dozen labels move them only a little. Kinds of evidence that should only help stay ≥ 0.
"""

from __future__ import annotations

import math

from .scoring import BIAS, WEIGHTS

FEATURES = ("anchors", "own", "shared", "people", "topic", "cross")
ADDED, OPENED, DISMISSED, PASSED = 1.0, 0.3, 1.0, 0.3
PRIOR = 10.0  # strength of the pull toward the hand-set weights, in labels' worth
MAX_FACTOR = 3.0  # a learned weight stays within [0, 3 × its hand-set value]
MIN_EACH = 3  # wanted and unwanted labels needed before fitting at all
KEEP_DAYS = 365


def label(paper: dict, added_on: str | None) -> tuple[float, float] | None:
    """(y, strength) for one stored paper (``day``, ``opened_at``, ``passed``, ``dismissed``), or
    None. ``added_on``: when the library item with this arXiv id was added (None: not there)."""
    if added_on and added_on[:10] >= paper["day"]:
        return 1.0, ADDED
    if paper["dismissed"]:
        return 0.0, DISMISSED
    if paper["opened_at"]:
        return 1.0, OPENED
    if paper["passed"]:
        return 0.0, PASSED
    return None


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (the system is small and positive definite)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in reversed(range(n)):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


def _bounds(k: str) -> tuple[float, float]:
    w0 = WEIGHTS[k]
    return (MAX_FACTOR * w0, 0.0) if w0 < 0 else (0.0, MAX_FACTOR * w0)


def fit(samples: list[tuple[dict, float, float]], *, bias: float = BIAS) -> dict | None:
    """``samples``: (features, y, strength). The learned weights, or None when there are too few
    wanted or unwanted labels to learn from."""
    pos = [s for s in samples if s[1] >= 0.5]
    neg = [s for s in samples if s[1] < 0.5]
    if len(pos) < MIN_EACH or len(neg) < MIN_EACH:
        return None
    total = sum(s[2] for s in samples)
    scale = {1: total / 2 / sum(s[2] for s in pos), 0: total / 2 / sum(s[2] for s in neg)}
    xs = [[float(f.get(k, 0.0)) for k in FEATURES] for f, _, _ in samples]
    ys = [y for _, y, _ in samples]
    ss = [s * scale[1 if y >= 0.5 else 0] for _, y, s in samples]
    prior = [WEIGHTS[k] for k in FEATURES]
    w = prior[:]
    n = len(FEATURES)
    for _ in range(25):
        grad = [PRIOR * (w[k] - prior[k]) for k in range(n)]
        hess = [[PRIOR if i == j else 0.0 for j in range(n)] for i in range(n)]
        for x, y, s in zip(xs, ys, ss, strict=True):
            z = bias + sum(wk * xk for wk, xk in zip(w, x, strict=True))
            p = 1 / (1 + math.exp(-max(-35.0, min(35.0, z))))
            for i in range(n):
                grad[i] += s * (p - y) * x[i]
                for j in range(n):
                    hess[i][j] += s * p * (1 - p) * x[i] * x[j]
        step = _solve(hess, grad)
        w = [wk - dk for wk, dk in zip(w, step, strict=True)]
        w = [min(max(wk, _bounds(k)[0]), _bounds(k)[1]) for wk, k in zip(w, FEATURES, strict=True)]
        if max(abs(d) for d in step) < 1e-6:
            break
    return {k: round(wk, 3) for k, wk in zip(FEATURES, w, strict=True)}
