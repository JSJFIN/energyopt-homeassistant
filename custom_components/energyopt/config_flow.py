"""Config flow for the EnergyOpt integration."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_API_KEY,
    CONF_ENABLE_CALENDARS,
    CONF_BASE_URL,
    CONF_POLL_INTERVAL,
    CONF_SITE_ID,
    DEFAULT_BASE_URL,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_SITE_ID,
    DOMAIN,
)

REQUEST_TIMEOUT = 30


class EnergyOptConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EnergyOpt."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EnergyOptOptionsFlow:
        """Return the options flow handler."""
        return EnergyOptOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].rstrip("/")
            site_id = user_input[CONF_SITE_ID]

            await self.async_set_unique_id(f"{base_url}:{site_id}")
            self._abort_if_unique_id_configured()

            error, site_name = await self._async_validate(
                base_url, user_input[CONF_API_KEY], site_id
            )
            if error is None:
                return self.async_create_entry(
                    title=site_name or f"EnergyOpt ({site_id[:8]})",
                    data={
                        CONF_BASE_URL: base_url,
                        CONF_API_KEY: user_input[CONF_API_KEY],
                        CONF_SITE_ID: site_id,
                        CONF_POLL_INTERVAL: user_input[CONF_POLL_INTERVAL],
                    },
                )
            errors["base"] = error

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_BASE_URL,
                    default=(user_input or {}).get(CONF_BASE_URL, DEFAULT_BASE_URL),
                ): str,
                vol.Required(CONF_API_KEY): str,
                vol.Required(
                    CONF_SITE_ID,
                    default=(user_input or {}).get(CONF_SITE_ID, DEFAULT_SITE_ID),
                ): str,
                vol.Required(
                    CONF_POLL_INTERVAL,
                    default=(user_input or {}).get(
                        CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                    ),
                ): int,
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def _async_validate(
        self, base_url: str, api_key: str, site_id: str
    ) -> tuple[str | None, str | None]:
        """Validate the connection by fetching the schedule endpoint once.

        Returns ``(error_key, site_name)``: error_key is None on success,
        site_name is the API-reported name used as the entry title.
        """
        session = async_get_clientsession(self.hass)
        url = f"{base_url}/v1/sites/{site_id}/schedule"
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                if response.status in (401, 403):
                    return "invalid_auth", None
                response.raise_for_status()
                data = await response.json()
        except aiohttp.ClientError:
            return "cannot_connect", None
        site_name = data.get("site_name") if isinstance(data, dict) else None
        return None, site_name

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of an existing entry."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].rstrip("/")
            site_id = user_input[CONF_SITE_ID]
            # An empty API key field means "keep the current key".
            api_key = user_input.get(CONF_API_KEY) or entry.data[CONF_API_KEY]

            new_unique_id = f"{base_url}:{site_id}"
            await self.async_set_unique_id(new_unique_id)
            # Don't let this entry be pointed at an already-configured site.
            for other in self._async_current_entries():
                if (
                    other.entry_id != entry.entry_id
                    and other.unique_id == new_unique_id
                ):
                    return self.async_abort(reason="already_configured")

            error, _ = await self._async_validate(base_url, api_key, site_id)
            if error is None:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=new_unique_id,
                    data={
                        CONF_BASE_URL: base_url,
                        CONF_API_KEY: api_key,
                        CONF_SITE_ID: site_id,
                        CONF_POLL_INTERVAL: entry.data.get(
                            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                        ),
                    },
                )
            errors["base"] = error

        prefill = user_input or {}
        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_BASE_URL,
                    default=prefill.get(
                        CONF_BASE_URL, entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
                    ),
                ): str,
                # Rendered as an empty password field: blank keeps the current key.
                vol.Optional(CONF_API_KEY): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Required(
                    CONF_SITE_ID,
                    default=prefill.get(
                        CONF_SITE_ID, entry.data.get(CONF_SITE_ID, DEFAULT_SITE_ID)
                    ),
                ): str,
            }
        )

        return self.async_show_form(
            step_id="reconfigure", data_schema=data_schema, errors=errors
        )


class EnergyOptOptionsFlow(OptionsFlow):
    """Handle the EnergyOpt options flow."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the poll interval."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_POLL_INTERVAL,
            self.config_entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
        )
        calendars = self.config_entry.options.get(CONF_ENABLE_CALENDARS, True)
        data_schema = vol.Schema(
            {
                vol.Required(CONF_POLL_INTERVAL, default=current): vol.All(
                    vol.Coerce(int), vol.Range(min=30)
                ),
                vol.Required(CONF_ENABLE_CALENDARS, default=calendars): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=data_schema)
