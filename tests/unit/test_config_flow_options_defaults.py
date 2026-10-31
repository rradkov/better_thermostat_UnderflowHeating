"""Options forms preserve settings regardless of their storage location."""

from unittest.mock import MagicMock

import pytest

from custom_components.better_thermostat.config_flow import OptionsFlowHandler
from custom_components.better_thermostat.utils.const import CONF_TOLERANCE
from custom_components.better_thermostat.utils.helpers import entry_settings


@pytest.mark.parametrize("data", [{}, {CONF_TOLERANCE: 0.2}])
def test_options_form_uses_the_resolved_settings_for_defaults(data):
    entry = MagicMock()
    entry.data = data
    entry.options = {CONF_TOLERANCE: 0.7}
    flow = OptionsFlowHandler(entry)
    flow.hass = MagicMock()
    flow.hass.states.get.return_value = None

    result = flow._show_user_form(entry_settings(entry), {}, {})

    defaults = {
        str(marker): marker.default()
        for marker in result["data_schema"].schema
        if callable(getattr(marker, "default", None))
    }
    assert defaults[CONF_TOLERANCE] == 0.7
