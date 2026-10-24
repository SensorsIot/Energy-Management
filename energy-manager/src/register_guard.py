"""Bounds how often a Huawei inverter holding register is written (FSD 4.7.1).

Every write to one of the three battery control registers costs a flash-erase
cycle, so `HAClient.set_number` already refuses to send a value the register
already holds. That guard is about the *value*; this one is about the *rate*.
A decision that changes legitimately but often would still chew the EEPROM, and
nothing else bounds it.

Two mechanisms, deliberately different in kind:

* a **dead zone** — a minimum interval between writes to the same register,
  which collapses a burst into one write;
* a **rolling 24 h budget** — the backstop that catches chatter a dead zone
  cannot bound, because a dead zone alone still permits a write every interval
  forever.

Both yield to a **protective** write. The protective direction differs per
register and is the caller's judgement, not this module's:

* discharge limit → ``0`` keeps the house battery out of the car; a block that
  is deferred is a block that did not happen;
* charge limit → ``charge_max_w`` releases charging; a limit left stuck at 0
  means a battery that never fills again;
* end-of-charge SOC → always, because the register is held at a constant and
  the only write that ever occurs is one restoring it after someone else
  lowered it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

DAY_S = 24 * 60 * 60


@dataclass
class RegisterWriteGuard:
    """Decide whether a register may be written now, and remember that it was.

    Call `allow()` before a write and `record()` only once a write has actually
    been sent. Recording a suppressed no-op would make the budget count writes
    the inverter never saw.
    """

    dead_zone_s: float = 60.0
    warn_per_day: int = 20
    max_per_day: int = 50

    _last_write_at: dict[str, float] = field(default_factory=dict)
    _recent: dict[str, list[float]] = field(default_factory=dict)
    _warned: set[str] = field(default_factory=set)

    def _prune(self, register: str, now: float) -> list[float]:
        """Drop writes older than the rolling window and return what is left."""
        kept = [t for t in self._recent.get(register, []) if now - t < DAY_S]
        self._recent[register] = kept
        return kept

    def writes_last_day(self, register: str) -> int:
        """How many writes this register has taken in the last 24 hours."""
        return len(self._prune(register, time.monotonic()))

    def allow(self, register: str, *, protective: bool = False) -> tuple[bool, str]:
        """Whether `register` may be written now.

        Returns (allowed, reason). The reason is empty when allowed and
        otherwise says which limit held the write back, so the caller can log
        something a human can act on.

        A protective write is always allowed and is still recorded, so the
        budget reflects every cycle the flash actually took.
        """
        now = time.monotonic()
        recent = self._prune(register, now)

        if protective:
            return True, ""

        last = self._last_write_at.get(register)
        if last is not None and now - last < self.dead_zone_s:
            return False, (
                f"dead zone — {now - last:.0f}s since the last write, "
                f"minimum {self.dead_zone_s:.0f}s"
            )

        if len(recent) >= self.max_per_day:
            return False, (
                f"daily budget — {len(recent)} writes in the last 24h, "
                f"maximum {self.max_per_day}"
            )

        return True, ""

    def record(self, register: str) -> str | None:
        """Note that `register` was just written.

        Returns a warning message the first time the register crosses
        `warn_per_day` within a window, else None. The warning fires once per
        crossing rather than on every write past it, so a genuinely busy day
        does not turn into a notification storm.
        """
        now = time.monotonic()
        self._last_write_at[register] = now
        count = len(self._prune(register, now)) + 1
        self._recent.setdefault(register, []).append(now)

        if count < self.warn_per_day:
            self._warned.discard(register)
            return None
        if register in self._warned:
            return None
        self._warned.add(register)
        return (
            f"{register} has taken {count} writes in the last 24h "
            f"(warning threshold {self.warn_per_day}, hard limit {self.max_per_day}). "
            f"Every write costs an inverter flash-erase cycle."
        )
