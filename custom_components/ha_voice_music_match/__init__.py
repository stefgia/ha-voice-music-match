"""Music Match: find what a misheard music request meant in the library."""

from __future__ import annotations

from homeassistant.components.media_player.const import INTENT_MEDIA_SEARCH_AND_PLAY
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import intent
from homeassistant.helpers.start import async_at_started

from .handler import MusicMatchSearchAndPlayHandler
from .library import MusicLibrary

type MusicMatchConfigEntry = ConfigEntry[MusicLibrary]


async def async_setup_entry(hass: HomeAssistant, entry: MusicMatchConfigEntry) -> bool:
    """Load the library and take over the play intent."""
    library = MusicLibrary(hass)
    entry.runtime_data = library
    entry.async_on_unload(library.async_start())

    @callback
    def _async_take_over(_hass: HomeAssistant) -> None:
        # After startup so media_player has registered the built-in handler,
        # which is kept to hand back on unload. HA logs an "is being
        # overwritten" warning here; that is this integration working.
        original = next(
            (h for h in intent.async_get(hass) if h.intent_type == INTENT_MEDIA_SEARCH_AND_PLAY),
            None,
        )
        intent.async_register(hass, MusicMatchSearchAndPlayHandler(entry))

        @callback
        def _async_hand_back() -> None:
            if original is not None:
                intent.async_register(hass, original)
            else:
                intent.async_remove(hass, INTENT_MEDIA_SEARCH_AND_PLAY)

        entry.async_on_unload(_async_hand_back)

    entry.async_on_unload(async_at_started(hass, _async_take_over))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MusicMatchConfigEntry) -> bool:
    """Undo setup (the unload callbacks do the work)."""
    return True
