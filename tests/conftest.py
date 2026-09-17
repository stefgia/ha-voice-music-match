"""Shared fixtures: a fake Music Assistant library and players in two areas."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_voice_music_match.const import DOMAIN

MA_PLAYER = "media_player.bedroom_speaker"
MA_PLAYER_2 = "media_player.living_room_speaker"
# Not Music Assistant, but also supports search, like a View Assist tablet.
TABLET = "media_player.bedroom_tablet"
OTHER_PLAYER = "media_player.office_tv"

LIBRARY: dict[str, list[dict[str, Any]]] = {
    "artist": [
        {"media_type": "artist", "name": "Gangstagrass", "uri": "library://artist/1"},
        {"media_type": "artist", "name": "Mozart", "uri": "library://artist/2"},
    ],
    "album": [
        {
            "media_type": "album",
            "name": "Rappalachia",
            "uri": "library://album/1",
            "artists": [{"name": "Gangstagrass"}],
        }
    ],
    "track": [
        {
            "media_type": "track",
            "name": "Long Hard Times to Come",
            "uri": "library://track/1",
            "artists": [{"name": "Gangstagrass"}],
        }
    ],
    "playlist": [{"media_type": "playlist", "name": "Morning Mix", "uri": "library://playlist/1"}],
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA load custom_components/ha_voice_music_match in every test."""
    return


@pytest.fixture
def library_calls() -> list[ServiceCall]:
    """Every music_assistant.get_library call the fake service received."""
    return []


@pytest.fixture
def fake_library(
    hass: HomeAssistant, library_calls: list[ServiceCall]
) -> Callable[[dict[str, list[dict[str, Any]]]], None]:
    """Register a loaded MA config entry and a get_library service over LIBRARY."""
    MockConfigEntry(domain="music_assistant", state=ConfigEntryState.LOADED).add_to_hass(hass)
    content = {key: list(value) for key, value in LIBRARY.items()}

    async def get_library(call: ServiceCall) -> dict[str, Any]:
        library_calls.append(call)
        items = content.get(call.data["media_type"], [])
        offset, limit = call.data["offset"], call.data["limit"]
        return {"items": items[offset : offset + limit], "limit": limit, "offset": offset}

    hass.services.async_register(
        "music_assistant", "get_library", get_library, supports_response=SupportsResponse.ONLY
    )

    def replace(new_content: dict[str, list[dict[str, Any]]]) -> None:
        content.clear()
        content.update(new_content)

    return replace


@pytest.fixture
def players(hass: HomeAssistant) -> None:
    """MA speakers in the Bedroom and Living Room, a tablet beside the Bedroom one, a TV alone in the Office."""
    areas = ar.async_get(hass)
    entities = er.async_get(hass)
    features = MediaPlayerEntityFeature.SEARCH_MEDIA | MediaPlayerEntityFeature.PLAY_MEDIA

    for entity_id, platform, area in (
        (MA_PLAYER, "music_assistant", "Bedroom"),
        (MA_PLAYER_2, "music_assistant", "Living Room"),
        (TABLET, "vaca", "Bedroom"),
        (OTHER_PLAYER, "androidtv", "Office"),
    ):
        area_entry = areas.async_get_or_create(area)
        object_id = entity_id.split(".")[1]
        entry = entities.async_get_or_create(
            "media_player", platform, object_id, suggested_object_id=object_id
        )
        entities.async_update_entity(entry.entity_id, area_id=area_entry.id)
        hass.states.async_set(entity_id, "idle", {"supported_features": features})


async def setup_integration(
    hass: HomeAssistant, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    """Set up core, then Music Match, and let the first library load finish."""
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(domain=DOMAIN, options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry
