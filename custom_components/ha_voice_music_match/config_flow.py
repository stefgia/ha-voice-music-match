"""Config and options flows for Music Match."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    LanguageSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TemplateSelector,
    TextSelector,
    TextSelectorConfig,
)

from .const import (
    CONF_ACT_THRESHOLD,
    CONF_ANSWERS_NO,
    CONF_ANSWERS_YES,
    CONF_ASK_THRESHOLD,
    CONF_LANGUAGE,
    CONF_REPLY_DID_YOU_MEAN,
    CONF_REPLY_NOT_FOUND,
    CONF_REPLY_PLAYING,
    DOMAIN,
)
from .settings import Settings

_THRESHOLD = NumberSelector(
    NumberSelectorConfig(min=0.0, max=1.0, step=0.01, mode=NumberSelectorMode.BOX)
)
_ANSWERS = TextSelector(TextSelectorConfig(multiple=True))


class MusicMatchConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add the integration. Everything else is in the options."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm and create the single entry."""
        if user_input is not None:
            return self.async_create_entry(title="Music Match", data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Language, thresholds and replies."""
        return MusicMatchOptionsFlow()


class MusicMatchOptionsFlow(OptionsFlow):
    """Every option on one form. Changes apply without a reload."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_ASK_THRESHOLD] > user_input[CONF_ACT_THRESHOLD]:
                errors[CONF_ASK_THRESHOLD] = "ask_above_act"
            else:
                return self.async_create_entry(data=user_input)

        current = Settings.from_options(self.hass, user_input or self.config_entry.options)
        schema = vol.Schema(
            {
                vol.Required(CONF_LANGUAGE, default=current.language): LanguageSelector(),
                vol.Required(CONF_ACT_THRESHOLD, default=current.act): _THRESHOLD,
                vol.Required(CONF_ASK_THRESHOLD, default=current.ask): _THRESHOLD,
                vol.Required(
                    CONF_REPLY_PLAYING, default=current.replies[CONF_REPLY_PLAYING]
                ): TemplateSelector(),
                vol.Required(
                    CONF_REPLY_NOT_FOUND, default=current.replies[CONF_REPLY_NOT_FOUND]
                ): TemplateSelector(),
                vol.Required(
                    CONF_REPLY_DID_YOU_MEAN, default=current.replies[CONF_REPLY_DID_YOU_MEAN]
                ): TemplateSelector(),
                vol.Required(CONF_ANSWERS_YES, default=current.answers_yes): _ANSWERS,
                vol.Required(CONF_ANSWERS_NO, default=current.answers_no): _ANSWERS,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
