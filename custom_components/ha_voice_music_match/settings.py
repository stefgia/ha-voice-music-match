"""The integration's options, with defaults filled in."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.template import Template

from .const import (
    CONF_ACT_THRESHOLD,
    CONF_ANSWERS_NO,
    CONF_ANSWERS_YES,
    CONF_ASK_THRESHOLD,
    CONF_LANGUAGE,
    CONF_REPLY_DID_YOU_MEAN,
    CONF_REPLY_NOT_FOUND,
    CONF_REPLY_PLAYING,
    DEFAULT_ANSWERS_NO,
    DEFAULT_ANSWERS_YES,
    DEFAULT_REPLY_DID_YOU_MEAN,
    DEFAULT_REPLY_NOT_FOUND,
    DEFAULT_REPLY_PLAYING,
)
from .matcher import ACT, ALBUM, ASK, TRACK, LibraryItem

_LOGGER = logging.getLogger(__name__)

_DEFAULT_REPLIES = {
    CONF_REPLY_PLAYING: DEFAULT_REPLY_PLAYING,
    CONF_REPLY_NOT_FOUND: DEFAULT_REPLY_NOT_FOUND,
    CONF_REPLY_DID_YOU_MEAN: DEFAULT_REPLY_DID_YOU_MEAN,
}


def primary_language(language: str) -> str:
    """"en-GB" and "en_US" both become "en"."""
    return language.replace("_", "-").split("-")[0].lower()


@dataclass(frozen=True)
class Settings:
    """What the handler needs from the options."""

    hass: HomeAssistant
    language: str
    act: float
    ask: float
    replies: Mapping[str, str]
    answers_yes: list[str]
    answers_no: list[str]

    @classmethod
    def from_options(cls, hass: HomeAssistant, options: Mapping[str, Any]) -> Settings:
        """Read the options; anything not set uses the default."""
        return cls(
            hass=hass,
            language=options.get(CONF_LANGUAGE) or hass.config.language,
            act=options.get(CONF_ACT_THRESHOLD, ACT),
            ask=options.get(CONF_ASK_THRESHOLD, ASK),
            replies={key: options.get(key) or default for key, default in _DEFAULT_REPLIES.items()},
            answers_yes=options.get(CONF_ANSWERS_YES) or DEFAULT_ANSWERS_YES,
            answers_no=options.get(CONF_ANSWERS_NO) or DEFAULT_ANSWERS_NO,
        )

    def handles(self, language: str) -> bool:
        """Whether a request in this language is matched or left to HA."""
        return primary_language(language) == primary_language(self.language)

    def reply(self, key: str, heard: str, item: LibraryItem, area: str | None) -> str:
        """Render one reply template. A broken template falls back to the default."""
        variables = {
            "heard": heard,
            "name": item.name,
            "artist": item.artists[0] if item.media_type in (TRACK, ALBUM) and item.artists else None,
            "media_type": item.media_type,
            "area": area,
        }
        try:
            return Template(self.replies[key], self.hass).async_render(
                variables, parse_result=False
            ).strip()
        except TemplateError as err:
            _LOGGER.warning("Reply template %s failed, using the default: %s", key, err)
            return Template(_DEFAULT_REPLIES[key], self.hass).async_render(
                variables, parse_result=False
            ).strip()
