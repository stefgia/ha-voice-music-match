"""Replacement HassMediaSearchAndPlay handler.

Corrects the search query against the library, then hands the request to the
built-in handler.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from typing import TYPE_CHECKING, override

from homeassistant.components.media_player.intent import MediaSearchAndPlayHandler
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent

from .const import (
    CONF_REPLY_DID_YOU_MEAN,
    CONF_REPLY_NOT_FOUND,
    CONF_REPLY_PLAYING,
    EVENT_DECISION,
    SATELLITE_IDLE_SETTLE,
    SATELLITE_IDLE_TIMEOUT,
)
from .matcher import MEDIA_TYPES, Band, LibraryItem, Match
from .settings import Settings

if TYPE_CHECKING:
    from . import MusicMatchConfigEntry

_LOGGER = logging.getLogger(__name__)

# The built-in "play ..." sentences can also say "music"; that means any type.
_ANY_MUSIC = "music"

type _Play = Callable[[intent.Intent], Awaitable[intent.IntentResponse]]


class MusicMatchSearchAndPlayHandler(MediaSearchAndPlayHandler):
    """Search-and-play that corrects names against the Music Assistant library."""

    def __init__(self, entry: MusicMatchConfigEntry) -> None:
        """Read the library and options from the entry on each request."""
        super().__init__()
        self._entry = entry

    @override
    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Correct the query, or pass the request on unchanged."""
        hass = intent_obj.hass
        play: _Play = super().async_handle
        settings = Settings.from_options(hass, self._entry.options)
        matcher = self._entry.runtime_data.matcher
        if matcher is None or not settings.handles(intent_obj.language):
            return await play(intent_obj)

        slots = self.async_validate_slots(intent_obj.slots)
        media_class: str | None = slots.get("media_class", {}).get("value")
        if media_class == _ANY_MUSIC:
            media_class = None
        if media_class is not None and media_class not in MEDIA_TYPES:
            return await play(intent_obj)

        heard: str = slots["search_query"]["value"]
        match = await hass.async_add_executor_job(
            matcher.match, heard, media_class, settings.act, settings.ask, settings.margin
        )
        _fire_decision(hass, intent_obj.context, heard, media_class, match)

        if match is None or match.band is Band.NONE:
            return await play(intent_obj)

        area: str | None = slots.get("area", {}).get("value")
        corrected = _with_item(intent_obj, match.item)
        if match.band is Band.ASK:
            response = intent_obj.create_response()
            not_found = settings.reply(CONF_REPLY_NOT_FOUND, heard, match.item, area)
            question = settings.reply(CONF_REPLY_DID_YOU_MEAN, heard, match.item, area)
            if intent_obj.satellite_id is None:
                # Typed, not spoken: nothing to listen for a yes on.
                response.async_set_speech(f"{not_found} {question}")
                return response
            response.async_set_speech(not_found)
            self._entry.async_create_background_task(
                hass,
                _async_confirm_and_play(
                    hass, intent_obj.satellite_id, question, settings, play, corrected
                ),
                "ha_voice_music_match did you mean",
            )
            return response

        response = await play(corrected)
        response.async_set_speech(settings.reply(CONF_REPLY_PLAYING, heard, match.item, area))
        return response


def _with_item(intent_obj: intent.Intent, item: LibraryItem) -> intent.Intent:
    """A copy of the intent that searches for the matched item by name and type."""
    slots = {
        **intent_obj.slots,
        "search_query": {"value": item.name, "text": item.name},
        "media_class": {"value": item.media_type, "text": item.media_type},
    }
    return intent.Intent(
        intent_obj.hass,
        intent_obj.platform,
        intent_obj.intent_type,
        slots,
        intent_obj.text_input,
        intent_obj.context,
        intent_obj.language,
        assistant=intent_obj.assistant,
        device_id=intent_obj.device_id,
        satellite_id=intent_obj.satellite_id,
        conversation_agent_id=intent_obj.conversation_agent_id,
    )


async def _async_wait_idle(hass: HomeAssistant, satellite_id: str) -> bool:
    """Wait until the satellite has finished the first reply."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SATELLITE_IDLE_TIMEOUT
    while loop.time() < deadline:
        state = hass.states.get(satellite_id)
        if state is not None and state.state == "idle":
            await asyncio.sleep(SATELLITE_IDLE_SETTLE)
            state = hass.states.get(satellite_id)
            if state is not None and state.state == "idle":
                return True
        await asyncio.sleep(0.2)
    return False


async def _async_confirm_and_play(
    hass: HomeAssistant,
    satellite_id: str,
    question: str,
    settings: Settings,
    play: _Play,
    corrected: intent.Intent,
) -> None:
    """Ask "did you mean", then play on a yes."""
    if not await _async_wait_idle(hass, satellite_id):
        _LOGGER.debug("Satellite %s never went idle; not asking", satellite_id)
        return
    name = corrected.slots["search_query"]["value"]
    try:
        answer = await hass.services.async_call(
            "assist_satellite",
            "ask_question",
            {
                "entity_id": satellite_id,
                "question": question,
                "preannounce": False,
                "answers": [
                    {"id": "yes", "sentences": settings.answers_yes},
                    {"id": "no", "sentences": settings.answers_no},
                ],
            },
            blocking=True,
            context=corrected.context,
            return_response=True,
        )
        if (answer or {}).get("id") == "yes":
            await play(corrected)
    except HomeAssistantError as err:
        _LOGGER.warning("Did-you-mean for %s failed: %s", name, err)


def _fire_decision(
    hass: HomeAssistant,
    context: Context,
    heard: str,
    media_class: str | None,
    match: Match | None,
) -> None:
    hass.bus.async_fire(
        EVENT_DECISION,
        {
            "heard": heard,
            "media_class": media_class,
            "band": match.band.value if match else Band.NONE.value,
            "matched": match.item.name if match else None,
            "matched_type": match.item.media_type if match else None,
            "uri": match.item.uri if match else None,
            "score": match.score if match else None,
            "runner_up": match.runner_up.name if match and match.runner_up else None,
            "runner_up_score": match.runner_up_score if match else None,
        },
        context=context,
    )
