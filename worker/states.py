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
* Head: Attentive/Neutral/Distracted from the pitch and yaw bands, measured from
  a reference direction that moves with the student (worker/front_region.py).
  Attentive is the front region: up to the student's up extent above the
  reference, up to `pitch_attentive_max_deg` below it, and within the sideways
  extent either way. The extents are the camera's limits (`pitch_attentive_min_deg`,
  `yaw_max_deg`) until enough has been learned. Looking further up or sideways
  than the extents is Distracted; below the attentive band is Neutral up to
  `pitch_neutral_max_deg`, then Distracted. The downward limits are never learned.
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
* A reading's band is fixed when it arrives, with the reference and extents as
  they were then. Learning from that reading changes later bands only, so
  seconds already counted are never rewritten.
* Only readings that are Attentive (the state, not just the band) and have a
  face teach the reference. `reset_learning` (recalibration) clears it.
* A reading whose timestamp is earlier than the previous one (clock stepped
  back) is treated as if it arrived at the previous timestamp.
"""

from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Any

from shared.schema import AttentionState
from worker.front_region import FrontRegionProfile, InMemoryFrontRegionProfile, LearningConfig
from worker.readings import Reading
from worker.tuned import TUNED_DIR, TunedFile, load_tuned

# A state that starts at a time and holds until the next transition.
Transition = tuple[float, AttentionState]

_ATT = AttentionState.ATTENTIVE
_NEU = AttentionState.NEUTRAL
_DIS = AttentionState.DISTRACTED
_NOT = AttentionState.NOT_VISIBLE


@dataclass(frozen=True)
class StateThresholds:
    pitch_attentive_min_deg: float  # the camera's limit: how far up still counts, before anything is learned
    pitch_attentive_max_deg: float  # fixed, never learned
    pitch_neutral_max_deg: float  # fixed, never learned
    yaw_max_deg: float  # the camera's limit for turning to either side, before anything is learned
    eyes_closed_grace_s: float
    look_down_allowance_s: float
    allowance_refill_s: float
    learning: LearningConfig
    tuned: TunedFile | None = field(default=None, compare=False)  # the camera's tuned file, when the limits came from one

    @classmethod
    def from_config(
        cls, raw: dict[str, Any], camera_id: str | None = None, tuned_dir: Path = TUNED_DIR
    ) -> "StateThresholds":
        """Read the top-level `states:` section of config/classpulse.yaml.

        With a `camera_id`, that camera's tuned file (config/tuned/<camera>.yaml, see worker/tuned.py)
        replaces the up limit, side limit and note/phone boundary when it exists. Nothing else changes,
        and other cameras are not affected. Only labelled tuning writes that file: the limits learned
        during a lecture (worker/front_region.py) never do.
        """
        section = raw["states"]
        names = ("pitch_attentive_min_deg", "pitch_attentive_max_deg", "pitch_neutral_max_deg", "yaw_max_deg")
        tuned = load_tuned(camera_id, tuned_dir) if camera_id is not None else None
        limits = {name: float(section[name]) for name in names}
        if tuned is not None:
            limits.update(tuned.values)
        return cls(
            pitch_attentive_min_deg=limits["pitch_attentive_min_deg"],
            pitch_attentive_max_deg=limits["pitch_attentive_max_deg"],
            pitch_neutral_max_deg=limits["pitch_neutral_max_deg"],
            yaw_max_deg=limits["yaw_max_deg"],
            eyes_closed_grace_s=float(section["eyes_closed_grace_s"]),
            look_down_allowance_s=float(section["look_down_allowance_s"]),
            allowance_refill_s=float(section["allowance_refill_s"]),
            learning=LearningConfig.from_config(section["learning"]),
            tuned=tuned,
        )


class StateClassifier:
    def __init__(self, thresholds: StateThresholds, profile: FrontRegionProfile | None = None) -> None:
        self._th = thresholds
        self._profile: FrontRegionProfile = profile or InMemoryFrontRegionProfile(thresholds.learning)
        self._held: Reading | None = None  # the reading currently being held
        self._held_band: AttentionState = _DIS  # its head-direction band, fixed when it arrived
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
        self._held_band = self._band(reading)
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
            if reading.face_present:  # learn after classifying: this reading's band is already fixed
                self._profile.learn(t, reading.pitch_deg, reading.yaw_deg)
        else:
            self._attentive_since = None
        self._state = state
        out.append((t, state))
        return out

    def close(self, timestamp: float) -> list[Transition]:
        """End of stream: the state changes up to `timestamp`, which hold until then."""
        return self._advance(max(timestamp, self._now))

    def reset_learning(self) -> None:
        """Forget the learned reference and extents (call on recalibration). Counted seconds are untouched."""
        self._profile.reset()

    def reference(self) -> tuple[float, float]:
        """The reference direction (pitch, yaw) relative to calibration, in degrees. Read-only; for displays."""
        return self._profile.reference()

    def extents(self) -> tuple[float, float]:
        """How far up and how far to either side of the reference still counts as Attentive, in degrees.

        The camera's limits from the config until enough has been learned. Read-only; for displays.
        """
        learned = self._profile.extents()
        if learned is not None:
            return learned
        return -self._th.pitch_attentive_min_deg, self._th.yaw_max_deg

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
        if r.face_present and self._held_band is _NEU:
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
        in_neutral_band = r.face_present and self._held_band is _NEU

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
        """Head direction alone, from the reference and extents as they are now: Attentive, Neutral or Distracted."""
        th = self._th
        ref_pitch, ref_yaw = self._profile.reference()
        up, side = self.extents()
        pitch = r.pitch_deg - ref_pitch
        if abs(r.yaw_deg - ref_yaw) > side:
            return _DIS
        if -up <= pitch <= th.pitch_attentive_max_deg:
            return _ATT
        if th.pitch_attentive_max_deg < pitch <= th.pitch_neutral_max_deg:
            return _NEU
        return _DIS

    def _state_at(self, r: Reading, x: float, s: float) -> AttentionState:
        """The state at time `x` while `r` is held, with the allowance as it was at `s` (when it was taken)."""
        th = self._th
        if not r.face_present:
            return _DIS if r.shoulders_present else _NOT
        if self._closed_since is not None and x >= self._closed_since + th.eyes_closed_grace_s:
            return _DIS
        band = self._held_band
        if band is _NEU and x >= s + (th.look_down_allowance_s - self._down_used):
            return _DIS
        return band


def limits_message(thresholds: StateThresholds, camera_id: str) -> str:
    """The start-up line saying whether this camera uses tuned limits or the defaults, and the values."""
    numbers = (
        f"up {-thresholds.pitch_attentive_min_deg:.1f}, side {thresholds.yaw_max_deg:.1f}, "
        f"notes/phone boundary {thresholds.pitch_neutral_max_deg:.1f} degrees"
    )
    tuned = thresholds.tuned
    if tuned is None:
        return f"Using the default limits for {camera_id} (no tuned file): {numbers}."
    made = tuned.created.astimezone(UTC).strftime("%Y-%m-%d")
    return f"Using tuned limits for {camera_id} from {tuned.path} (made {made}): {numbers}."
