"""The cached library, loaded through the Music Assistant integration.

Reading it through `music_assistant.get_library` means no Navidrome or MA
credentials: whatever MA has in its library is what can be matched.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import MUSIC_ASSISTANT_DOMAIN, PAGE_SIZE, REFRESH_INTERVAL, RETRY_DELAY
from .matcher import MEDIA_TYPES, LibraryItem, Matcher

_LOGGER = logging.getLogger(__name__)


def _item_from_dict(data: dict[str, Any]) -> LibraryItem | None:
    name, uri, media_type = data.get("name"), data.get("uri"), data.get("media_type")
    if not (name and uri and media_type):
        return None
    artists = tuple(a["name"] for a in data.get("artists") or () if a.get("name"))
    return LibraryItem(media_type=str(media_type), name=name, uri=uri, artists=artists)


class MusicLibrary:
    """Holds the matcher and keeps it fresh."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Start empty; `async_start` schedules the loads."""
        self.hass = hass
        self.matcher: Matcher | None = None
        self.loaded_at: datetime | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._retry: CALLBACK_TYPE | None = None

    @callback
    def async_start(self) -> Callable[[], None]:
        """Refresh now and hourly. Returns a callable that stops it."""
        self.hass.async_create_background_task(self.async_refresh(), "ha_voice_music_match refresh")
        self._unsubs.append(
            async_track_time_interval(self.hass, self._async_scheduled, REFRESH_INTERVAL)
        )
        return self.async_stop

    @callback
    def async_stop(self) -> None:
        """Cancel scheduled refreshes."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._retry:
            self._retry()
            self._retry = None

    async def _async_scheduled(self, _now: datetime | None = None) -> None:
        self._retry = None
        await self.async_refresh()

    async def async_refresh(self) -> None:
        """Reload every Music Assistant library into a new matcher."""
        entries = self.hass.config_entries.async_loaded_entries(MUSIC_ASSISTANT_DOMAIN)
        items: list[LibraryItem] = []
        try:
            for entry in entries:
                for media_type in MEDIA_TYPES:
                    items.extend(await self._async_fetch(entry.entry_id, media_type))
        except HomeAssistantError as err:
            _LOGGER.warning("Could not load the Music Assistant library: %s", err)
            items = []

        if not items:
            # Keep the last good library; retry sooner if there never was one.
            if self.matcher is None and self._retry is None:
                self._retry = async_call_later(self.hass, RETRY_DELAY, self._async_scheduled)
            return

        # Big libraries take seconds to index, so build off the event loop.
        self.matcher = await self.hass.async_add_executor_job(Matcher, items)
        self.loaded_at = dt_util.utcnow()
        _LOGGER.debug("Loaded %s library items for matching", len(self.matcher))

    async def _async_fetch(self, entry_id: str, media_type: str) -> list[LibraryItem]:
        items: list[LibraryItem] = []
        offset = 0
        while True:
            response = await self.hass.services.async_call(
                MUSIC_ASSISTANT_DOMAIN,
                "get_library",
                {
                    "config_entry_id": entry_id,
                    "media_type": media_type,
                    "limit": PAGE_SIZE,
                    "offset": offset,
                },
                blocking=True,
                return_response=True,
            )
            page = (response or {}).get("items") or []
            items.extend(item for data in page if (item := _item_from_dict(data)))
            if len(page) < PAGE_SIZE:
                return items
            offset += PAGE_SIZE
