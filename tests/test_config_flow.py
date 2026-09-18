"""Config flow tests."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_voice_music_match.const import (
    CONF_ACT_THRESHOLD,
    CONF_ANSWERS_NO,
    CONF_ANSWERS_YES,
    CONF_ASK_THRESHOLD,
    CONF_LANGUAGE,
    CONF_MARGIN,
    CONF_REPLY_DID_YOU_MEAN,
    CONF_REPLY_NOT_FOUND,
    CONF_REPLY_PLAYING,
    DEFAULT_ANSWERS_NO,
    DEFAULT_REPLY_DID_YOU_MEAN,
    DEFAULT_REPLY_NOT_FOUND,
    DOMAIN,
)


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    with patch("custom_components.ha_voice_music_match.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Music Match"


async def test_single_instance(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


def _options(**changes: object) -> dict[str, object]:
    return {
        CONF_LANGUAGE: "en",
        CONF_ACT_THRESHOLD: 0.8,
        CONF_ASK_THRESHOLD: 0.6,
        CONF_MARGIN: 0.05,
        CONF_REPLY_PLAYING: "Now playing {{ name }}",
        CONF_REPLY_NOT_FOUND: DEFAULT_REPLY_NOT_FOUND,
        CONF_REPLY_DID_YOU_MEAN: DEFAULT_REPLY_DID_YOU_MEAN,
        CONF_ANSWERS_YES: ["yes", "go on"],
        CONF_ANSWERS_NO: DEFAULT_ANSWERS_NO,
    } | changes


async def test_options_flow_saves(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults[CONF_ACT_THRESHOLD] == 0.70
    assert defaults[CONF_ASK_THRESHOLD] == 0.62
    assert defaults[CONF_MARGIN] == 0.08
    assert defaults[CONF_LANGUAGE] == hass.config.language

    result = await hass.config_entries.options.async_configure(result["flow_id"], _options())
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == _options()


async def test_options_flow_rejects_ask_above_act(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _options(**{CONF_ASK_THRESHOLD: 0.9})
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ASK_THRESHOLD: "ask_above_act"}
    assert entry.options == {}
