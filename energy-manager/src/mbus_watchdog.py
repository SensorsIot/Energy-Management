"""M-Bus grid-meter staleness watchdog (FSD 4.7.5).

The control loop already falls back to the DTSU meter when the M-Bus reading is
older than 20 s, but that fallback is silent — a day-long M-Bus outage went
unnoticed. This module is a pure, side-effect-free state machine that decides
when a *prolonged* staleness deserves a one-shot Telegram alert (and a recovery
notice when the meter returns). run.py wires the edges to Telegram; the tests
exercise the logic without an HA client.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MbusWatchdog:
    """Track continuous M-Bus staleness and emit one-shot alert edges.

    Feed every grid read into ``update(fresh, now_ts)``. Brief gaps (the control
    loop's own 20 s fallback) never alert; only a continuous stale stretch of at
    least ``alert_after_s`` raises the warning, and it raises exactly once per
    episode. The meter coming back raises a single recovery edge.

    Attributes:
        alert_after_s: How long the meter must be continuously stale before the
            warning fires.
        stale_since: Timestamp (epoch seconds) of the first stale read in the
            current episode, or ``None`` while fresh.
        alerted: Whether the warning has already fired for the current episode.

    """

    alert_after_s: float = 300.0
    stale_since: float | None = None
    alerted: bool = False

    def update(self, fresh: bool, now_ts: float) -> str:
        """Advance the state machine with the latest read.

        Args:
            fresh: True if the M-Bus reading is fresh (<20 s), False otherwise
                (stale, unavailable, or entity missing).
            now_ts: Current time in epoch seconds.

        Returns:
            The edge to act on: ``"stale"`` (raise the warning now),
            ``"recovered"`` (clear a previously raised warning), or ``""``
            (nothing changed).

        """
        if fresh:
            recovered = self.alerted
            self.stale_since = None
            self.alerted = False
            return "recovered" if recovered else ""

        if self.stale_since is None:
            self.stale_since = now_ts
        if not self.alerted and (now_ts - self.stale_since) >= self.alert_after_s:
            self.alerted = True
            return "stale"
        return ""

    def stale_seconds(self, now_ts: float) -> float:
        """Return how long the meter has been continuously stale, in seconds."""
        return 0.0 if self.stale_since is None else now_ts - self.stale_since
