"""Pure unit tests for utils/boost_heater.py's BoostHeaterTracker.

Covers the symmetric heat/cool direction logic: a room that got colder than
the threshold arms a heat boost, a room that got warmer arms a cool boost,
and a direction the device doesn't support is never armed.
"""

from __future__ import annotations

from homeassistant.components.climate.const import HVACMode

from custom_components.better_thermostat.utils.boost_heater import BoostHeaterTracker


def test_record_window_open_stores_the_temperature():
    tracker = BoostHeaterTracker()
    tracker.record_window_open(24.0)
    assert tracker.open_temp == 24.0


def test_a_drop_beyond_threshold_arms_a_heat_boost():
    tracker = BoostHeaterTracker()
    tracker.record_window_open(23.0)
    direction = tracker.evaluate_on_close(
        17.5,
        threshold_heat=5.0,
        threshold_cool=5.0,
        supports_heat=True,
        supports_cool=True,
    )  # dropped 5.5°C
    assert direction == HVACMode.HEAT
    assert tracker.boost_direction == HVACMode.HEAT
    assert tracker.boost_active is True


def test_a_rise_beyond_threshold_arms_a_cool_boost():
    tracker = BoostHeaterTracker()
    tracker.record_window_open(23.0)
    direction = tracker.evaluate_on_close(
        29.0,
        threshold_heat=5.0,
        threshold_cool=5.0,
        supports_heat=True,
        supports_cool=True,
    )  # rose 6.0°C
    assert direction == HVACMode.COOL
    assert tracker.boost_direction == HVACMode.COOL
    assert tracker.boost_active is True


def test_a_small_drift_in_either_direction_does_not_arm():
    tracker = BoostHeaterTracker()
    tracker.record_window_open(23.0)
    direction = tracker.evaluate_on_close(
        21.0,
        threshold_heat=5.0,
        threshold_cool=5.0,
        supports_heat=True,
        supports_cool=True,
    )  # only 2.0°C drop
    assert direction is None
    assert tracker.boost_active is False


def test_exact_threshold_match_does_not_arm():
    """The comparison is strictly greater-than, so an exact match doesn't arm."""
    tracker = BoostHeaterTracker()
    tracker.record_window_open(23.0)
    direction = tracker.evaluate_on_close(
        18.0,
        threshold_heat=5.0,
        threshold_cool=5.0,
        supports_heat=True,
        supports_cool=True,
    )  # exactly 5.0°C drop
    assert direction is None


def test_unsupported_direction_is_never_armed():
    """A device that can't heat never gets a heat boost, even past threshold."""
    tracker = BoostHeaterTracker()
    tracker.record_window_open(23.0)
    direction = tracker.evaluate_on_close(
        15.0,
        threshold_heat=5.0,
        threshold_cool=5.0,
        supports_heat=False,
        supports_cool=True,
    )
    assert direction is None
    assert tracker.boost_active is False


def test_evaluate_on_close_consumes_the_open_temp_even_when_it_does_not_arm():
    """A close without a matching open (or a second close) can't re-arm on stale data."""
    tracker = BoostHeaterTracker()
    tracker.record_window_open(23.0)
    tracker.evaluate_on_close(
        22.0,
        threshold_heat=5.0,
        threshold_cool=5.0,
        supports_heat=True,
        supports_cool=True,
    )
    assert tracker.open_temp is None
    direction_again = tracker.evaluate_on_close(
        10.0,
        threshold_heat=5.0,
        threshold_cool=5.0,
        supports_heat=True,
        supports_cool=True,
    )
    assert direction_again is None


def test_evaluate_on_close_with_no_recorded_open_never_arms():
    tracker = BoostHeaterTracker()
    direction = tracker.evaluate_on_close(
        10.0,
        threshold_heat=5.0,
        threshold_cool=5.0,
        supports_heat=True,
        supports_cool=True,
    )
    assert direction is None


def test_evaluate_on_close_with_none_temperatures_never_arms():
    tracker = BoostHeaterTracker()
    tracker.record_window_open(None)
    assert (
        tracker.evaluate_on_close(
            20.0,
            threshold_heat=5.0,
            threshold_cool=5.0,
            supports_heat=True,
            supports_cool=True,
        )
        is None
    )

    tracker2 = BoostHeaterTracker()
    tracker2.record_window_open(23.0)
    assert (
        tracker2.evaluate_on_close(
            None,
            threshold_heat=5.0,
            threshold_cool=5.0,
            supports_heat=True,
            supports_cool=True,
        )
        is None
    )


def test_clear_resets_boost_cycle_state_but_keeps_slow_ema():
    tracker = BoostHeaterTracker()
    tracker.boost_direction = HVACMode.HEAT
    tracker.previous_fan_mode = "high"
    tracker.boost_started_ts = 123.0
    tracker.update_slow_ema(21.0, now_ts=100.0)

    tracker.clear()

    assert tracker.boost_direction is None
    assert tracker.boost_active is False
    assert tracker.previous_fan_mode is None
    assert tracker.boost_started_ts is None
    # The slow EMA is a standing trend signal, not boost-cycle-scoped state.
    assert tracker.slow_ema == 21.0


def test_update_slow_ema_seeds_on_first_call():
    tracker = BoostHeaterTracker()
    tracker.update_slow_ema(20.0, now_ts=0.0)
    assert tracker.slow_ema == 20.0


def test_update_slow_ema_moves_toward_new_value_over_time():
    tracker = BoostHeaterTracker()
    tracker.update_slow_ema(20.0, now_ts=0.0)
    tracker.update_slow_ema(25.0, now_ts=7200.0)  # 2 hours later, tau=3600s
    assert 24.0 < tracker.slow_ema <= 25.0


def test_update_slow_ema_ignores_none_values():
    tracker = BoostHeaterTracker()
    tracker.update_slow_ema(20.0, now_ts=0.0)
    tracker.update_slow_ema(None, now_ts=60.0)
    assert tracker.slow_ema == 20.0
