"""Attention state rules: timed readings in, attention states out. Pure; no camera.

How time works
--------------
Readings are sample-and-hold: a reading's state is credited from its timestamp
until the next reading (WindowAggregator does the crediting). Several rules
depend on how long something has lasted, and the moment a rule trips can fall
*inside* a held interval (a sparse stream, or a stall). So `observe` does not
return one state per reading: it returns the state changes (time, state) that
happened since the previous call, in time order, ending with the state that
starts at this reading. Feed them to `WindowAggregator.observe` in order. A
state is only ever emitted for time that has not been credited yet, so seconds
already counted are never rewritten.

Rules (face, then eyes, then head direction)
--------------------------------------------
* Face absent: Distracted if shoulders are present, else Not visible.
* Eyes closed: continuous closure under `eyes_closed_grace_s` is ignored (the
  head decides). From the grace period onward it is Distracted.
* Head: Attentive/Neutral/Distracted from the pitch and yaw bands. Yaw beyond
  the limit, or pitch outside the attentive and neutral bands, is Distracted.
* Looking down: time spent in the Neutral band uses up `look_down_allowance_s`
  of allowance. Once it is spent, the Neutral band is Distracted. The allowance
  refills only after `allowance_refill_s` of continuous Attentive.

Decisions on cases the ticket leaves open
-----------------------------------------
* Face lost during a look-down: the allowance is kept as it is. Without a face
  nothing is observed, so no allowance is used, and the lost face is not
  Attentive, so it never refills. A face that returns in the Neutral band
  carries on with the remaining allowance.
* Face lost during eye closure: eyes cannot be seen without a face, so the
  closure run ends. If the face returns with the eyes still closed, the grace
  period starts again from that moment.
* Eyes closed while looking down: the head still counts as looking down, so the
  allowance keeps being used even once closure has made the state Distracted.
* Head far outside the Neutral band (for example pitch above the neutral
  maximum) is Distracted and neither uses nor refills the allowance.
* "Continuous" closure means consecutive closed readings; under sample-and-hold
  a closed reading is taken to stay closed until the next reading.
* A reading whose timestamp is earlier than the previous one (clock stepped
  back) is treated as if it arrived at the previous timestamp.
"""

from dataclasses import dataclass
from typing import Any

from shared.schema import AttentionState
from worker.readings import Reading

# A state that starts at a time and holds until the next transition.
Transition = tuple[float, AttentionState]

_ATT = AttentionState.ATTENTIVE
_NEU = AttentionState.NEUTRAL
_DIS = AttentionState.DISTRACTED
_NOT = AttentionState.NOT_VISIBLE


@dataclass(frozen=True)
class StateThresholds:
    pitch_attentive_min_deg: float
    pitch_attentive_max_deg: float
    pitch_neutral_max_deg: float
    yaw_max_deg: float
    eyes_closed_grace_s: float
    look_down_allowance_s: float
    allowance_refill_s: float

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> "StateThresholds":
        """Read the top-level `states:` section of config/classpulse.yaml."""
        section = raw["states"]
        return cls(**{name: float(section[name]) for name in cls.__dataclass_fields__})


class StateClassifier:
    def __init__(self, thresholds: StateThresholds) -> None:
        self._th = thresholds
        self._held: Reading | None = None  # the reading currently being held
        self._now = 0.0  # time up to which transitions have been emitted
        self._state: AttentionState | None = None  # the state in force at _now
        self._closed_since: float | None = None  # start of the current closed-eyes run
        self._attentive_since: float | None = None  # start of the current Attentive run
        self._down_used = 0.0  # allowance used so far, as of _now

    def observe(self, reading: Reading) -> list[Transition]:
        """Take the next reading. Returns the state changes since the last call."""
        t = max(reading.timestamp, self._now)
        out = self._advance(t)
        self._held = reading
        self._now = t
        if reading.face_present and reading.eyes_closed:
            if self._closed_since is None:
                self._closed_since = t
        else:
            self._closed_since = None
        state = self._state_at(reading, t, t)
        if state is _ATT:
            if self._attentive_since is None:
                self._attentive_since = t
        else:
            self._attentive_since = None
        self._state = state
        out.append((t, state))
        return out

    def close(self, timestamp: float) -> list[Transition]:
        """End of stream: the state changes up to `timestamp`, which hold until then."""
        return self._advance(max(timestamp, self._now))

    def current_state(self, at: float) -> AttentionState | None:
        """The state in force at time `at` (not before the last reading), or None before any reading.

        Read-only; for displays. It does not change what later readings produce.
        """
        if self._held is None:
            return None
        return self._state_at(self._held, max(at, self._now), self._now)

    def look_down_remaining_s(self, at: float) -> float:
        """Seconds of look-down allowance left at time `at`, counting the current hold. Read-only."""
        th = self._th
        r = self._held
        if r is None:
            return th.look_down_allowance_s
        at = max(at, self._now)
        if (
            self._attentive_since is not None
            and self.current_state(at) is _ATT
            and at - self._attentive_since >= th.allowance_refill_s
        ):
            return th.look_down_allowance_s  # long enough attentive: the allowance has refilled
        used = self._down_used
        if r.face_present and self._band(r) is _NEU:
            used += at - self._now
        return max(0.0, th.look_down_allowance_s - used)

    # -- internals ---------------------------------------------------------------

    def _advance(self, t: float) -> list[Transition]:
        """Move time on to `t` with the held reading unchanged; return the changes inside (now, t)."""
        r = self._held
        s = self._now
        out: list[Transition] = []
        if r is None or t <= s:
            return out
        th = self._th
        in_neutral_band = r.face_present and self._band(r) is _NEU

        # Moments inside the interval where the held reading changes its state.
        trips: list[float] = []
        if self._closed_since is not None:
            trips.append(self._closed_since + th.eyes_closed_grace_s)
        if in_neutral_band:
            trips.append(s + (th.look_down_allowance_s - self._down_used))
        previous = self._state
        for b in sorted(trips):
            if not s < b < t:
                continue
            state = self._state_at(r, b, s)
            if state is not previous:
                if previous is _ATT:
                    self._end_attentive_run(b)
                out.append((b, state))
                previous = state

        if in_neutral_band:
            self._down_used += t - s
        self._now = t
        self._state = previous
        if previous is _ATT:  # an Attentive run that is still going counts towards a refill
            self._refill_if_long_enough(t)
        return out

    def _end_attentive_run(self, t: float) -> None:
        self._refill_if_long_enough(t)
        self._attentive_since = None

    def _refill_if_long_enough(self, t: float) -> None:
        if self._attentive_since is not None and t - self._attentive_since >= self._th.allowance_refill_s:
            self._down_used = 0.0

    def _band(self, r: Reading) -> AttentionState:
        """Head direction alone: Attentive, Neutral or Distracted."""
        th = self._th
        if abs(r.yaw_deg) > th.yaw_max_deg:
            return _DIS
        if th.pitch_attentive_min_deg <= r.pitch_deg <= th.pitch_attentive_max_deg:
            return _ATT
        if th.pitch_attentive_max_deg < r.pitch_deg <= th.pitch_neutral_max_deg:
            return _NEU
        return _DIS

    def _state_at(self, r: Reading, x: float, s: float) -> AttentionState:
        """The state at time `x` while `r` is held, with the allowance as it was at `s` (when it was taken)."""
        th = self._th
        if not r.face_present:
            return _DIS if r.shoulders_present else _NOT
        if self._closed_since is not None and x >= self._closed_since + th.eyes_closed_grace_s:
            return _DIS
        band = self._band(r)
        if band is _NEU and x >= s + (th.look_down_allowance_s - self._down_used):
            return _DIS
        return band
