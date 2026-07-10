"""Config flow for the EnergyOpt integration."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_API_KEY,
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
