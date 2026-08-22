"""Boost's cooldown guard and asymmetric heat/cool thresholds.

Cooldown: protects the shared device from short-cycling when a room keeps
drifting past the threshold in quick succession (e.g. repeated window
in/out). Asymmetric threshold: some setups want a tighter trigger for a
cold drop (comfort-sensitive in winter) than for a heat gain (less urgent
in summer) - a separate optional cool threshold, defaulting to mirror the
heat one.
"""

from __future__ import annotations

from homeassistant.components.climate.const import HVACMode

from custom_components.better_thermostat.utils.boost_heater import BoostHeaterTracker


def test_asymmetric_thresholds_use_the_matching_direction():
    tracker = BoostHeaterTracker()
    tracker.record_window_open(23.0)
    # 3.0 drop: below the 5.0 heat threshold, so no heat boost arms.
    direction = tracker.evaluate_on_close(
        20.0,
        threshold_heat=5.0,
        threshold_cool=2.0,
        supports_heat=True,
        supports_cool=True,
    )
    assert direction is None

    tracker.record_window_open(23.0)
    # 3.0 rise: above the 2.0 cool threshold, so a cool boost arms even
    # though it wouldn't have against the (higher) heat threshold.
    direction = tracker.evaluate_on_close(
        26.0,
        threshold_heat=5.0,
        threshold_cool=2.0,
        supports_heat=True,
        supports_cool=True,
    )
    assert direction == HVACMode.COOL


def test_symmetric_default_behaves_like_the_single_threshold_it_replaced():
    tracker = BoostHeaterTracker()
    tracker.record_window_open(23.0)
    direction = tracker.evaluate_on_close(
        17.5,
        threshold_heat=5.0,
        threshold_cool=5.0,
        supports_heat=True,
        supports_cool=True,
    )
    assert direction == HVACMode.HEAT


def test_a_close_inside_the_cooldown_window_does_not_rearm():
    tracker = BoostHeaterTracker()
    tracker.boost_started_ts = 100.0
    tracker.clear(now_ts=200.0)  # last_boost_end_ts = 200.0
    assert tracker.last_boost_end_ts == 200.0

    tracker.record_window_open(23.0)
    direction = tracker.evaluate_on_close(
        17.0,  # a 6.0 drop, well past threshold
        threshold_heat=5.0,
        threshold_cool=5.0,
        supports_heat=True,
        supports_cool=True,
        now_ts=250.0,  # only 50s after the previous boost ended
        cooldown_s=900.0,  # 15 minutes
    )
    assert direction is None
    assert tracker.boost_active is False
    # The temperature is still consumed, so a rapid follow-up close can't
    # reuse a stale drop once the cooldown expires.
    assert tracker.open_temp is None


def test_a_close_after_the_cooldown_window_rearms_normally():
    tracker = BoostHeaterTracker()
    tracker.boost_started_ts = 100.0
    tracker.clear(now_ts=200.0)

    tracker.record_window_open(23.0)
    direction = tracker.evaluate_on_close(
        17.0,
        threshold_heat=5.0,
        threshold_cool=5.0,
        supports_heat=True,
        supports_cool=True,
        now_ts=1200.0,  # 1000s after, past the 900s cooldown
        cooldown_s=900.0,
    )
    assert direction == HVACMode.HEAT


def test_zero_cooldown_never_blocks_rearming():
    tracker = BoostHeaterTracker()
    tracker.boost_started_ts = 100.0
    tracker.clear(now_ts=200.0)

    tracker.record_window_open(23.0)
    direction = tracker.evaluate_on_close(
        17.0,
        threshold_heat=5.0,
        threshold_cool=5.0,
        supports_heat=True,
        supports_cool=True,
        now_ts=200.5,
        cooldown_s=0.0,
    )
    assert direction == HVACMode.HEAT


def test_clear_only_starts_the_cooldown_clock_if_the_boost_actually_started():
    """An armed-but-never-actuated boost (e.g. device unavailable throughout)
    must not start a cooldown that blocks a legitimate next arm."""
    tracker = BoostHeaterTracker()
    assert tracker.boost_started_ts is None
    tracker.clear(now_ts=200.0)
    assert tracker.last_boost_end_ts is None
