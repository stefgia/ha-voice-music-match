"""The cached library, loaded through the Music Assistant integration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_time_interval

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
        self._by_entry: dict[str, list[LibraryItem]] = {}
        self._unsub_interval: CALLBACK_TYPE | None = None
        self._retry: CALLBACK_TYPE | None = None
        self._stopped = False

    @callback
    def async_start(self) -> Callable[[], None]:
        """Refresh now and hourly. Returns a callable that stops it."""
        self.hass.async_create_background_task(self.async_refresh(), "ha_voice_music_match refresh")
        self._unsub_interval = async_track_time_interval(
            self.hass, self._async_scheduled, REFRESH_INTERVAL
        )
        return self.async_stop

    @callback
    def async_stop(self) -> None:
        """Cancel scheduled refreshes."""
        self._stopped = True
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None
        self._cancel_retry()

    @callback
    def _cancel_retry(self) -> None:
        if self._retry:
            self._retry()
            self._retry = None

    async def _async_scheduled(self, _now: datetime | None = None) -> None:
        self._cancel_retry()
        await self.async_refresh()

    async def async_refresh(self) -> None:
        """Reload every Music Assistant library into a new matcher.

        A server that fails keeps the items from its last successful load.
        """
        by_entry: dict[str, list[LibraryItem]] = {}
        failed_without_items = False
        for entry in self.hass.config_entries.async_loaded_entries(MUSIC_ASSISTANT_DOMAIN):
            try:
                by_entry[entry.entry_id] = [
                    item
                    for media_type in MEDIA_TYPES
                    for item in await self._async_fetch(entry.entry_id, media_type)
                ]
            except HomeAssistantError as err:
                _LOGGER.warning("Could not load the library from %s: %s", entry.title, err)
                if entry.entry_id in self._by_entry:
                    by_entry[entry.entry_id] = self._by_entry[entry.entry_id]
                else:
                    failed_without_items = True

        if self._stopped:
            return
        self._by_entry = by_entry
        items = [item for entry_items in by_entry.values() for item in entry_items]
        if self._retry is None and (failed_without_items or (not items and self.matcher is None)):
            self._retry = async_call_later(self.hass, RETRY_DELAY, self._async_scheduled)
        if not items:
            return

        # Big libraries take seconds to index, so build off the event loop.
        self.matcher = await self.hass.async_add_executor_job(Matcher, items)
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
