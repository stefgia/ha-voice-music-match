"""The replacement for HA's built-in HassMediaSearchAndPlay handler.

HA's handler hands the query to `media_player.search_media` verbatim, so a
misheard name finds nothing. This subclass keeps the built-in sentences, target
resolution and behaviour, and only steps in when the target is a Music
Assistant player and the request is in the configured language: it matches
the query against the library and plays the item's URI directly. Anything
else goes to the built-in code unchanged.

Both paths reach it: HA's own agent matching "play ..." sentences, and an LLM
agent in prefer-local mode, which HA sends every "play ..." to and which calls
this intent as a tool.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, override

from homeassistant.components.media_player import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    MediaPlayerEntityFeature,
)
from homeassistant.components.media_player.intent import MediaSearchAndPlayHandler
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    intent,
)

from .const import (
    CONF_REPLY_DID_YOU_MEAN,
    CONF_REPLY_NOT_FOUND,
    CONF_REPLY_PLAYING,
    EVENT_DECISION,
    MUSIC_ASSISTANT_DOMAIN,
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


class MusicMatchSearchAndPlayHandler(MediaSearchAndPlayHandler):
    """Search-and-play that corrects names against the Music Assistant library."""

    def __init__(self, entry: MusicMatchConfigEntry) -> None:
        """Keep the entry: its runtime data is the library, its options the settings."""
        super().__init__()
        self._entry = entry

    @override
    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Match and play, or fall through to the built-in handler."""
        hass = intent_obj.hass
        settings = Settings.from_options(hass, self._entry.options)
        if not settings.handles(intent_obj.language):
            return await super().async_handle(intent_obj)

        slots = self.async_validate_slots(intent_obj.slots)
        media_class: str | None = slots.get("media_class", {}).get("value")
        if media_class == _ANY_MUSIC:
            media_class = None

        matcher = self._entry.runtime_data.matcher
        target = _match_target(intent_obj, slots)
        if (
            matcher is None
            or target is None
            or (media_class is not None and media_class not in MEDIA_TYPES)
        ):
            return await super().async_handle(intent_obj)

        heard: str = slots["search_query"]["value"]
        match = await hass.async_add_executor_job(
            matcher.match, heard, media_class, settings.act, settings.ask
        )
        _fire_decision(hass, intent_obj.context, heard, media_class, target, match)

        if match is None or match.band is Band.NONE:
            # Nothing plausible in the library: MA's own search, as before.
            return await super().async_handle(intent_obj)

        response = intent_obj.create_response()
        area = _area_name(hass, target.entity_id)
        if match.band is Band.ASK:
            not_found = settings.reply(CONF_REPLY_NOT_FOUND, heard, match.item, area)
            question = settings.reply(CONF_REPLY_DID_YOU_MEAN, heard, match.item, area)
            if intent_obj.satellite_id is None:
                # Typed, not spoken: nothing to listen for a yes on.
                response.async_set_speech(f"{not_found} {question}")
                return response
            response.async_set_speech(not_found)
            hass.async_create_background_task(
                _async_confirm_and_play(
                    hass,
                    intent_obj.context,
                    intent_obj.satellite_id,
                    question,
                    settings,
                    target,
                    match.item,
                ),
                "ha_voice_music_match did you mean",
            )
            return response

        await _async_play(hass, intent_obj.context, target, match.item)
        response.async_set_speech(settings.reply(CONF_REPLY_PLAYING, heard, match.item, area))
        response.async_set_speech_slots({"media": {"title": match.item.name}})
        return response


def _match_target(intent_obj: intent.Intent, slots: dict[str, Any]) -> State | None:
    """Resolve the Music Assistant player a music request is for.

    Like the built-in handler, but a room holding several players that can
    search (a View Assist tablet next to the speaker, say) is not a failure:
    a music request means the Music Assistant one. Returns None when no single
    Music Assistant player fits; the built-in handler then runs and raises its
    own error.
    """
    hass = intent_obj.hass
    preferred_area_id = slots.get("preferred_area_id", {}).get("value")
    constraints = intent.MatchTargetsConstraints(
        name=slots.get("name", {}).get("value"),
        area_name=slots.get("area", {}).get("value"),
        floor_name=slots.get("floor", {}).get("value"),
        domains={MEDIA_PLAYER_DOMAIN},
        assistant=intent_obj.assistant,
        features=MediaPlayerEntityFeature.SEARCH_MEDIA | MediaPlayerEntityFeature.PLAY_MEDIA,
        single_target=True,
    )
    result = intent.async_match_targets(
        hass,
        constraints,
        intent.MatchTargetsPreferences(
            area_id=preferred_area_id,
            floor_id=slots.get("preferred_floor_id", {}).get("value"),
        ),
    )
    if not (
        result.is_match or result.no_match_reason is intent.MatchFailedReason.MULTIPLE_TARGETS
    ):
        return None

    players = [s for s in result.states if _is_music_assistant_player(hass, s.entity_id)]
    if len(players) > 1 and preferred_area_id:
        players = [s for s in players if _area_id(hass, s.entity_id) == preferred_area_id]
    return players[0] if len(players) == 1 else None


def _is_music_assistant_player(hass: HomeAssistant, entity_id: str) -> bool:
    entry = er.async_get(hass).async_get(entity_id)
    return entry is not None and entry.platform == MUSIC_ASSISTANT_DOMAIN


def _area_id(hass: HomeAssistant, entity_id: str) -> str | None:
    """The entity's area, or its device's when the entity has none of its own."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return None
    if entry.area_id is None and entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        return device.area_id if device else None
    return entry.area_id


def _area_name(hass: HomeAssistant, entity_id: str) -> str | None:
    area_id = _area_id(hass, entity_id)
    area = ar.async_get(hass).async_get_area(area_id) if area_id else None
    return area.name if area else None


async def _async_play(
    hass: HomeAssistant, context: Context, target: State, item: LibraryItem
) -> None:
    try:
        await hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            "play_media",
            {
                "entity_id": target.entity_id,
                "media_content_id": item.uri,
                "media_content_type": item.media_type,
            },
            blocking=True,
            context=context,
        )
    except HomeAssistantError as err:
        raise intent.IntentHandleError(f"Error playing {item.name}: {err}") from err


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
    context: Context,
    satellite_id: str,
    question: str,
    settings: Settings,
    target: State,
    item: LibraryItem,
) -> None:
    """Ask "did you mean", then play on a yes."""
    if not await _async_wait_idle(hass, satellite_id):
        _LOGGER.debug("Satellite %s never went idle; not asking", satellite_id)
        return
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
            context=context,
            return_response=True,
        )
        if (answer or {}).get("id") != "yes":
            return
        await _async_play(hass, context, target, item)
    except (HomeAssistantError, intent.IntentHandleError) as err:
        _LOGGER.warning("Did-you-mean for %s failed: %s", item.name, err)


def _fire_decision(
    hass: HomeAssistant,
    context: Context,
    heard: str,
    media_class: str | None,
    target: State,
    match: Match | None,
) -> None:
    hass.bus.async_fire(
        EVENT_DECISION,
        {
            "heard": heard,
            "media_class": media_class,
            "target": target.entity_id,
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
