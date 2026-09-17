"""Library loading through music_assistant.get_library."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.ha_voice_music_match.const import PAGE_SIZE

from .conftest import LIBRARY, setup_integration


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
