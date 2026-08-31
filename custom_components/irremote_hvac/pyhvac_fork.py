"""Local pyhvac fork used by the climate entity.

The upstream package is still used for config-flow probing so that model
detection remains based on the raw generated buffer. This fork only
normalizes Daikin timing output on the runtime send path.
"""

from __future__ import annotations

from pyhvac import irhvac as _upstream_irhvac
from pyhvac.irhvac import *

_DAIKIN_PROTOCOLS = {
    DAIKIN,
    DAIKIN2,
    DAIKIN216,
    DAIKIN160,
    DAIKIN176,
    DAIKIN128,
    DAIKIN152,
    DAIKIN64,
    DAIKIN200,
    DAIKIN312,
}


def _compress_repeated_timings(timings: list[int]) -> list[int]:
    """Collapse exact repeated timing blocks into one base block."""
    total_length = len(timings)

    for block_length in range(1, total_length // 2 + 1):
        if total_length % block_length != 0:
            continue

        candidate = timings[:block_length]
        repeat_count = total_length // block_length
        if candidate * repeat_count == timings:
            return candidate

    return timings


class IRac(_upstream_irhvac.IRac):
    """Upstream IRac with Daikin timing normalization."""

    def getTiming(self):
        timings = list(super().getTiming())
        if self.next.protocol in _DAIKIN_PROTOCOLS:
            timings = _compress_repeated_timings(timings)
        return timings