"""The replacement play handler, end to end inside a test HA."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.media_player.const import INTENT_MEDIA_SEARCH_AND_PLAY
from homeassistant.components.media_player.intent import MediaSearchAndPlayHandler
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import area_registry as ar, intent
from pytest_homeassistant_custom_component.common import async_capture_events, async_mock_service

from custom_components.ha_voice_music_match.const import (
    CONF_ANSWERS_YES,
    CONF_LANGUAGE,
    CONF_REPLY_NOT_FOUND,
    CONF_REPLY_PLAYING,
    EVENT_DECISION,
)
from custom_components.ha_voice_music_match.handler import MusicMatchSearchAndPlayHandler
from custom_components.ha_voice_music_match.matcher import ARTIST, Band, LibraryItem, Match

from .conftest import MA_PLAYER, MA_PLAYER_2, setup_integration

pytestmark = pytest.mark.usefixtures("fake_library", "players")


def _handler(hass: HomeAssistant) -> intent.IntentHandler:
    return next(h for h in intent.async_get(hass) if h.intent_type == INTENT_MEDIA_SEARCH_AND_PLAY)


async def _play(hass: HomeAssistant, slots: dict[str, Any], **kwargs: Any) -> intent.IntentResponse:
    return await intent.async_handle(
        hass,
        "test",
        INTENT_MEDIA_SEARCH_AND_PLAY,
        {key: {"value": value} for key, value in slots.items()},
        **kwargs,
    )


async def test_takes_over_and_hands_back(hass: HomeAssistant) -> None:
    """The built-in handler is replaced while loaded and restored on unload."""
    entry = await setup_integration(hass)
    assert isinstance(_handler(hass), MusicMatchSearchAndPlayHandler)

    assert await hass.config_entries.async_unload(entry.entry_id)
    handler = _handler(hass)
    assert isinstance(handler, MediaSearchAndPlayHandler)
    assert not isinstance(handler, MusicMatchSearchAndPlayHandler)


async def test_misheard_artist_plays_library_item(hass: HomeAssistant) -> None:
    """The Bedroom also holds a searchable tablet: the MA speaker is still chosen."""
    await setup_integration(hass)
    play = async_mock_service(hass, "media_player", "play_media")
    events = async_capture_events(hass, EVENT_DECISION)

    response = await _play(hass, {"search_query": "Gaza Grass", "area": "Bedroom"})

    assert len(play) == 1
    assert play[0].data["entity_id"] == MA_PLAYER
    assert play[0].data["media_content_id"] == "library://artist/1"
    assert play[0].data["media_content_type"] == "artist"
    assert response.speech["plain"]["speech"] == "Playing Gangstagrass in the bedroom"
    assert events[0].data["heard"] == "Gaza Grass"
    assert events[0].data["matched"] == "Gangstagrass"
    assert events[0].data["band"] == Band.ACT.value


async def test_preferred_area_is_used_without_a_room(hass: HomeAssistant) -> None:
    """"Play X" with no room plays in the satellite's area."""
    await setup_integration(hass)
    play = async_mock_service(hass, "media_player", "play_media")
    area_id = ar.async_get(hass).async_get_area_by_name("Bedroom").id

    await _play(hass, {"search_query": "gangsta grass", "preferred_area_id": area_id})

    assert play[0].data["media_content_id"] == "library://artist/1"


async def test_song_reply_names_the_artist(hass: HomeAssistant) -> None:
    await setup_integration(hass)
    async_mock_service(hass, "media_player", "play_media")

    response = await _play(
        hass, {"search_query": "long hard times to come", "media_class": "track", "area": "Bedroom"}
    )

    assert (
        response.speech["plain"]["speech"]
        == "Playing Long Hard Times to Come by Gangstagrass in the bedroom"
    )


async def test_non_music_assistant_player_uses_builtin(hass: HomeAssistant) -> None:
    await setup_integration(hass)
    builtin = AsyncMock(return_value=intent.IntentResponse("en"))

    with patch.object(MediaSearchAndPlayHandler, "async_handle", builtin):
        await _play(hass, {"search_query": "Gaza Grass", "area": "Office"})

    builtin.assert_awaited_once()


async def test_non_music_media_class_uses_builtin(hass: HomeAssistant) -> None:
    await setup_integration(hass)
    builtin = AsyncMock(return_value=intent.IntentResponse("en"))

    with patch.object(MediaSearchAndPlayHandler, "async_handle", builtin):
        await _play(hass, {"search_query": "Gaza Grass", "media_class": "movie", "area": "Bedroom"})

    builtin.assert_awaited_once()


async def test_no_plausible_match_uses_builtin_search(hass: HomeAssistant) -> None:
    await setup_integration(hass)
    builtin = AsyncMock(return_value=intent.IntentResponse("en"))
    events = async_capture_events(hass, EVENT_DECISION)

    with patch.object(MediaSearchAndPlayHandler, "async_handle", builtin):
        await _play(hass, {"search_query": "xylophone quartet zzz", "area": "Bedroom"})

    builtin.assert_awaited_once()
    assert events[0].data["band"] == Band.NONE.value


def _ask_match() -> Match:
    return Match(LibraryItem(ARTIST, "Gangstagrass", "library://artist/1"), 0.65, Band.ASK)


async def test_low_confidence_typed_asks_in_reply(hass: HomeAssistant) -> None:
    await setup_integration(hass)
    play = async_mock_service(hass, "media_player", "play_media")

    with patch("custom_components.ha_voice_music_match.matcher.Matcher.match", return_value=_ask_match()):
        response = await _play(hass, {"search_query": "gasket grass", "area": "Bedroom"})

    assert not play
    assert response.speech["plain"]["speech"] == (
        "I couldn't find gasket grass. Did you mean Gangstagrass?"
    )


@pytest.mark.parametrize(("answer", "played"), [("yes", 1), ("no", 0)])
async def test_low_confidence_spoken_asks_then_plays_on_yes(
    hass: HomeAssistant, answer: str, played: int
) -> None:
    await setup_integration(hass)
    play = async_mock_service(hass, "media_player", "play_media")
    hass.states.async_set("assist_satellite.bedroom_voice", "idle")
    questions: list[ServiceCall] = []

    async def ask_question(call: ServiceCall) -> dict[str, Any]:
        questions.append(call)
        return {"id": answer}

    hass.services.async_register(
        "assist_satellite", "ask_question", ask_question, supports_response=SupportsResponse.ONLY
    )

    with (
        patch("custom_components.ha_voice_music_match.matcher.Matcher.match", return_value=_ask_match()),
        patch("custom_components.ha_voice_music_match.handler.SATELLITE_IDLE_SETTLE", 0),
    ):
        response = await _play(
            hass,
            {"search_query": "gasket grass", "area": "Bedroom"},
            satellite_id="assist_satellite.bedroom_voice",
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert response.speech["plain"]["speech"] == "I couldn't find gasket grass."
    assert questions[0].data["question"] == "Did you mean Gangstagrass?"
    assert questions[0].data["entity_id"] == "assist_satellite.bedroom_voice"
    assert len(play) == played


async def test_satellite_room_picks_between_speakers(hass: HomeAssistant) -> None:
    """No room said: of several MA speakers, the one in the satellite's room plays."""
    await setup_integration(hass)
    play = async_mock_service(hass, "media_player", "play_media")
    living_room = ar.async_get(hass).async_get_area_by_name("Living Room").id

    await _play(hass, {"search_query": "gangsta grass", "preferred_area_id": living_room})

    assert play[0].data["entity_id"] == MA_PLAYER_2


async def test_several_speakers_and_no_room_uses_builtin(hass: HomeAssistant) -> None:
    """Typed with no room: no single speaker, so HA's handler gives its own error."""
    await setup_integration(hass)
    builtin = AsyncMock(return_value=intent.IntentResponse("en"))

    with patch.object(MediaSearchAndPlayHandler, "async_handle", builtin):
        await _play(hass, {"search_query": "gangsta grass"})

    builtin.assert_awaited_once()


async def test_request_in_another_language_uses_builtin(hass: HomeAssistant) -> None:
    await setup_integration(hass)
    builtin = AsyncMock(return_value=intent.IntentResponse("de"))
    events = async_capture_events(hass, EVENT_DECISION)

    with patch.object(MediaSearchAndPlayHandler, "async_handle", builtin):
        await _play(hass, {"search_query": "Gaza Grass", "area": "Bedroom"}, language="de")

    builtin.assert_awaited_once()
    assert not events


async def test_configured_language_is_matched(hass: HomeAssistant) -> None:
    """A German setup matches German requests, including regional tags."""
    await setup_integration(hass, {CONF_LANGUAGE: "de"})
    play = async_mock_service(hass, "media_player", "play_media")

    await _play(hass, {"search_query": "Gaza Grass", "area": "Bedroom"}, language="de-AT")

    assert play[0].data["media_content_id"] == "library://artist/1"


async def test_custom_replies(hass: HomeAssistant) -> None:
    await setup_integration(
        hass,
        {
            CONF_REPLY_PLAYING: "Spiele {{ name }}{% if area %} im {{ area }}{% endif %}",
            CONF_REPLY_NOT_FOUND: "{{ heard }}? Hmm.",
        },
    )
    async_mock_service(hass, "media_player", "play_media")

    response = await _play(hass, {"search_query": "Gaza Grass", "area": "Bedroom"})
    assert response.speech["plain"]["speech"] == "Spiele Gangstagrass im Bedroom"

    with patch("custom_components.ha_voice_music_match.matcher.Matcher.match", return_value=_ask_match()):
        response = await _play(hass, {"search_query": "gasket grass", "area": "Bedroom"})
    assert response.speech["plain"]["speech"] == "gasket grass? Hmm. Did you mean Gangstagrass?"


async def test_broken_reply_template_falls_back_to_default(hass: HomeAssistant) -> None:
    await setup_integration(hass, {CONF_REPLY_PLAYING: "Playing {{ name | no_such_filter }}"})
    async_mock_service(hass, "media_player", "play_media")

    response = await _play(hass, {"search_query": "Gaza Grass", "area": "Bedroom"})

    assert response.speech["plain"]["speech"] == "Playing Gangstagrass in the bedroom"


async def test_custom_yes_answers_are_listened_for(hass: HomeAssistant) -> None:
    await setup_integration(hass, {CONF_ANSWERS_YES: ["ja"]})
    async_mock_service(hass, "media_player", "play_media")
    hass.states.async_set("assist_satellite.bedroom_voice", "idle")
    questions: list[ServiceCall] = []

    async def ask_question(call: ServiceCall) -> dict[str, Any]:
        questions.append(call)
        return {"id": "no"}

    hass.services.async_register(
        "assist_satellite", "ask_question", ask_question, supports_response=SupportsResponse.ONLY
    )

    with (
        patch("custom_components.ha_voice_music_match.matcher.Matcher.match", return_value=_ask_match()),
        patch("custom_components.ha_voice_music_match.handler.SATELLITE_IDLE_SETTLE", 0),
    ):
        await _play(
            hass,
            {"search_query": "gasket grass", "area": "Bedroom"},
            satellite_id="assist_satellite.bedroom_voice",
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert questions[0].data["answers"][0] == {"id": "yes", "sentences": ["ja"]}
