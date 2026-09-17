"""Library loading through music_assistant.get_library."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.ha_voice_music_match.const import PAGE_SIZE
from custom_components.ha_voice_music_match.library import MusicLibrary

from .conftest import LIBRARY, setup_integration


def _two_servers(
    hass: HomeAssistant,
) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    """Two MA servers with one artist each: their artists by entry id, and a set of failing ids."""
    failing: set[str] = set()
    artists: dict[str, list[dict[str, Any]]] = {}
    for name in ("First Band", "Second Band"):
        entry = MockConfigEntry(domain="music_assistant", title=name, state=ConfigEntryState.LOADED)
        entry.add_to_hass(hass)
        artists[entry.entry_id] = [{"media_type": "artist", "name": name, "uri": f"{name}:1"}]

    async def get_library(call: ServiceCall) -> dict[str, Any]:
        entry_id = call.data["config_entry_id"]
        if entry_id in failing:
            raise HomeAssistantError("offline")
        return {"items": artists[entry_id] if call.data["media_type"] == "artist" else []}

    hass.services.async_register(
        "music_assistant", "get_library", get_library, supports_response=SupportsResponse.ONLY
    )
    return artists, failing


async def test_loads_every_media_type(
    hass: HomeAssistant, fake_library, library_calls: list[ServiceCall]
) -> None:
    entry = await setup_integration(hass)

    assert {call.data["media_type"] for call in library_calls} == {
        "artist",
        "album",
        "track",
        "playlist",
    }
    assert len(entry.runtime_data.matcher) == sum(len(v) for v in LIBRARY.values())


async def test_pages_through_large_libraries(
    hass: HomeAssistant, fake_library, library_calls: list[ServiceCall]
) -> None:
    tracks: list[dict[str, Any]] = [
        {"media_type": "track", "name": f"Song {i}", "uri": f"library://track/{i}"}
        for i in range(PAGE_SIZE + 1)
    ]
    fake_library({"track": tracks})

    entry = await setup_integration(hass)

    track_calls = [c for c in library_calls if c.data["media_type"] == "track"]
    assert [c.data["offset"] for c in track_calls] == [0, PAGE_SIZE]
    assert len(entry.runtime_data.matcher) == PAGE_SIZE + 1


async def test_hourly_refresh_picks_up_new_music(
    hass: HomeAssistant, fake_library, library_calls: list[ServiceCall]
) -> None:
    entry = await setup_integration(hass)
    fake_library(
        {**LIBRARY, "artist": [{"media_type": "artist", "name": "New Band", "uri": "x:1"}]}
    )

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=1, seconds=1))
    await hass.async_block_till_done(wait_background_tasks=True)

    match = entry.runtime_data.matcher.match("new band")
    assert match is not None
    assert match.item.uri == "x:1"


async def test_without_music_assistant_retries_and_keeps_builtin(hass: HomeAssistant) -> None:
    """No MA loaded: no matcher yet, and a retry is scheduled."""
    with patch("custom_components.ha_voice_music_match.library.async_call_later") as call_later:
        entry = await setup_integration(hass)

    assert entry.runtime_data.matcher is None
    call_later.assert_called_once()


async def test_refresh_after_stop_schedules_no_retry(hass: HomeAssistant) -> None:
    """A refresh still running at unload must not leave a retry behind."""
    library = MusicLibrary(hass)
    with patch("custom_components.ha_voice_music_match.library.async_call_later") as call_later:
        library.async_start()
        await hass.async_block_till_done(wait_background_tasks=True)
        library.async_stop()
        await library.async_refresh()

    call_later.assert_called_once()
    call_later.return_value.assert_called_once()


async def test_one_failing_server_does_not_block_the_others(hass: HomeAssistant) -> None:
    artists, failing = _two_servers(hass)
    failing.add(list(artists)[1])

    with patch("custom_components.ha_voice_music_match.library.async_call_later") as call_later:
        entry = await setup_integration(hass)

    assert len(entry.runtime_data.matcher) == 1
    call_later.assert_called_once()


async def test_failing_server_keeps_its_last_items(hass: HomeAssistant) -> None:
    artists, failing = _two_servers(hass)
    first, second = artists
    entry = await setup_integration(hass)
    artists[first].append({"media_type": "artist", "name": "New Band", "uri": "New Band:1"})
    failing.add(second)

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=1, seconds=1))
    await hass.async_block_till_done(wait_background_tasks=True)

    matcher = entry.runtime_data.matcher
    assert len(matcher) == 3
    assert matcher.match("second band").item.uri == "Second Band:1"
    assert matcher.match("new band").item.uri == "New Band:1"
