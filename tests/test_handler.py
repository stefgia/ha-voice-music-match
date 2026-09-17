"""The replacement play handler, inside a test HA."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.media_player import BrowseMedia, MediaClass, SearchMedia
from homeassistant.components.media_player.const import INTENT_MEDIA_SEARCH_AND_PLAY
from homeassistant.components.media_player.intent import MediaSearchAndPlayHandler
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import intent
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

from .conftest import PLAYER, setup_integration

pytestmark = pytest.mark.usefixtures("fake_library", "player")

SATELLITE = "assist_satellite.bedroom_voice"


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


@pytest.fixture
def builtin() -> Iterator[AsyncMock]:
    """The built-in handler, recording the intent it receives."""
    mock = AsyncMock(side_effect=lambda intent_obj: intent_obj.create_response())
    with patch.object(MediaSearchAndPlayHandler, "async_handle", mock):
        yield mock


def _sent(builtin: AsyncMock) -> dict[str, Any]:
    """The slot values the built-in handler was last called with."""
    return {key: slot["value"] for key, slot in builtin.await_args.args[0].slots.items()}


def _ask_match() -> Match:
    return Match(LibraryItem(ARTIST, "Gangstagrass", "library://artist/1"), 0.65, Band.ASK)


def _register_ask_question(hass: HomeAssistant, answer: str) -> list[ServiceCall]:
    questions: list[ServiceCall] = []

    async def ask_question(call: ServiceCall) -> dict[str, Any]:
        questions.append(call)
        return {"id": answer}

    hass.services.async_register(
        "assist_satellite", "ask_question", ask_question, supports_response=SupportsResponse.ONLY
    )
    return questions


async def test_takes_over_and_hands_back(hass: HomeAssistant) -> None:
    """The built-in handler is replaced while loaded and restored on unload."""
    entry = await setup_integration(hass)
    assert isinstance(_handler(hass), MusicMatchSearchAndPlayHandler)

    assert await hass.config_entries.async_unload(entry.entry_id)
    handler = _handler(hass)
    assert isinstance(handler, MediaSearchAndPlayHandler)
    assert not isinstance(handler, MusicMatchSearchAndPlayHandler)


async def test_corrected_name_is_searched_and_played(hass: HomeAssistant) -> None:
    """End to end through the built-in handler's search and play."""
    await setup_integration(hass)
    result = BrowseMedia(
        media_class=MediaClass.ARTIST,
        media_content_id="library://artist/1",
        media_content_type="artist",
        title="Gangstagrass",
        can_play=True,
        can_expand=False,
    )
    searches = async_mock_service(
        hass,
        "media_player",
        "search_media",
        response={PLAYER: SearchMedia(result=[result])},
        supports_response=SupportsResponse.ONLY,
    )
    plays = async_mock_service(hass, "media_player", "play_media")
    events = async_capture_events(hass, EVENT_DECISION)

    response = await _play(hass, {"search_query": "Gaza Grass", "area": "Bedroom"})

    assert searches[0].data["search_query"] == "Gangstagrass"
    assert searches[0].data["media_filter_classes"] == ["artist"]
    assert plays[0].data["media_content_id"] == "library://artist/1"
    assert response.speech["plain"]["speech"] == "Playing Gangstagrass in the bedroom"
    assert events[0].data["heard"] == "Gaza Grass"
    assert events[0].data["matched"] == "Gangstagrass"
    assert events[0].data["band"] == Band.ACT.value


async def test_song_reply_names_the_artist(hass: HomeAssistant, builtin: AsyncMock) -> None:
    await setup_integration(hass)

    response = await _play(hass, {"search_query": "long hard times to come", "media_class": "track"})

    assert _sent(builtin)["search_query"] == "Long Hard Times to Come"
    assert response.speech["plain"]["speech"] == "Playing Long Hard Times to Come by Gangstagrass"


async def test_other_slots_are_passed_on(hass: HomeAssistant, builtin: AsyncMock) -> None:
    await setup_integration(hass)

    await _play(hass, {"search_query": "some music by gangsta grass", "name": "Speaker"})

    assert _sent(builtin) == {
        "search_query": "Gangstagrass",
        "media_class": "artist",
        "name": "Speaker",
    }


async def test_non_music_media_class_is_left_alone(hass: HomeAssistant, builtin: AsyncMock) -> None:
    await setup_integration(hass)
    events = async_capture_events(hass, EVENT_DECISION)

    await _play(hass, {"search_query": "Gaza Grass", "media_class": "movie"})

    assert _sent(builtin) == {"search_query": "Gaza Grass", "media_class": "movie"}
    assert not events


async def test_no_plausible_match_is_left_alone(hass: HomeAssistant, builtin: AsyncMock) -> None:
    await setup_integration(hass)
    events = async_capture_events(hass, EVENT_DECISION)

    await _play(hass, {"search_query": "xylophone quartet zzz"})

    assert _sent(builtin) == {"search_query": "xylophone quartet zzz"}
    assert events[0].data["band"] == Band.NONE.value


async def test_low_confidence_typed_asks_in_reply(hass: HomeAssistant, builtin: AsyncMock) -> None:
    await setup_integration(hass)

    with patch("custom_components.ha_voice_music_match.matcher.Matcher.match", return_value=_ask_match()):
        response = await _play(hass, {"search_query": "gasket grass"})

    builtin.assert_not_awaited()
    assert response.speech["plain"]["speech"] == (
        "I couldn't find gasket grass. Did you mean Gangstagrass?"
    )


@pytest.mark.parametrize(("answer", "played"), [("yes", True), ("no", False)])
async def test_low_confidence_spoken_asks_then_plays_on_yes(
    hass: HomeAssistant, builtin: AsyncMock, answer: str, played: bool
) -> None:
    await setup_integration(hass)
    hass.states.async_set(SATELLITE, "idle")
    questions = _register_ask_question(hass, answer)

    with (
        patch("custom_components.ha_voice_music_match.matcher.Matcher.match", return_value=_ask_match()),
        patch("custom_components.ha_voice_music_match.handler.SATELLITE_IDLE_SETTLE", 0),
    ):
        response = await _play(hass, {"search_query": "gasket grass"}, satellite_id=SATELLITE)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert response.speech["plain"]["speech"] == "I couldn't find gasket grass."
    assert questions[0].data["question"] == "Did you mean Gangstagrass?"
    assert questions[0].data["entity_id"] == SATELLITE
    assert builtin.await_count == played
    if played:
        assert _sent(builtin)["search_query"] == "Gangstagrass"


async def test_request_in_another_language_is_left_alone(
    hass: HomeAssistant, builtin: AsyncMock
) -> None:
    await setup_integration(hass)
    events = async_capture_events(hass, EVENT_DECISION)

    await _play(hass, {"search_query": "Gaza Grass"}, language="de")

    assert _sent(builtin) == {"search_query": "Gaza Grass"}
    assert not events


async def test_configured_language_is_matched(hass: HomeAssistant, builtin: AsyncMock) -> None:
    """A German setup matches German requests, including regional tags."""
    await setup_integration(hass, {CONF_LANGUAGE: "de"})

    await _play(hass, {"search_query": "Gaza Grass"}, language="de-AT")

    assert _sent(builtin)["search_query"] == "Gangstagrass"


async def test_custom_replies(hass: HomeAssistant, builtin: AsyncMock) -> None:
    await setup_integration(
        hass,
        {
            CONF_REPLY_PLAYING: "Spiele {{ name }}{% if area %} im {{ area }}{% endif %}",
            CONF_REPLY_NOT_FOUND: "{{ heard }}? Hmm.",
        },
    )

    response = await _play(hass, {"search_query": "Gaza Grass", "area": "Bedroom"})
    assert response.speech["plain"]["speech"] == "Spiele Gangstagrass im Bedroom"

    with patch("custom_components.ha_voice_music_match.matcher.Matcher.match", return_value=_ask_match()):
        response = await _play(hass, {"search_query": "gasket grass"})
    assert response.speech["plain"]["speech"] == "gasket grass? Hmm. Did you mean Gangstagrass?"


async def test_broken_reply_template_falls_back_to_default(
    hass: HomeAssistant, builtin: AsyncMock
) -> None:
    await setup_integration(hass, {CONF_REPLY_PLAYING: "Playing {{ name | no_such_filter }}"})

    response = await _play(hass, {"search_query": "Gaza Grass", "area": "Bedroom"})

    assert response.speech["plain"]["speech"] == "Playing Gangstagrass in the bedroom"


async def test_custom_yes_answers_are_listened_for(hass: HomeAssistant, builtin: AsyncMock) -> None:
    await setup_integration(hass, {CONF_ANSWERS_YES: ["ja"]})
    hass.states.async_set(SATELLITE, "idle")
    questions = _register_ask_question(hass, "no")

    with (
        patch("custom_components.ha_voice_music_match.matcher.Matcher.match", return_value=_ask_match()),
        patch("custom_components.ha_voice_music_match.handler.SATELLITE_IDLE_SETTLE", 0),
    ):
        await _play(hass, {"search_query": "gasket grass"}, satellite_id=SATELLITE)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert questions[0].data["answers"][0] == {"id": "yes", "sentences": ["ja"]}
