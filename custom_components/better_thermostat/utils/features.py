"""Release-level availability of experimental integration features."""

from typing import Final

# Production suspension: secondary-device Boost is retained for development,
# but no configuration, diagnostic, event, or service may activate it here.
# This does not affect Home Assistant's standard temperature preset Boost.
SECONDARY_BOOST_AVAILABLE: Final = False
