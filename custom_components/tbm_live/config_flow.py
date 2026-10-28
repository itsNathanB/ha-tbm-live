"""Assistant de configuration : choix de la ligne, puis de l'arrêt et du sens."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import Line, StopChoice, TbmClient, TbmConnectionError, TbmError
from .const import (
    CONF_CHOICE,
    CONF_DESTINATION_REF,
    CONF_DIRECTION_NAME,
    CONF_DIRECTION_REF,
    CONF_LINE_MODE,
    CONF_LINE_NAME,
    CONF_LINE_REF,
    CONF_PHYSICAL,
    CONF_STOP_NAME,
    CONF_STOP_REF,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class TbmConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ajout d'un arrêt suivi (une entrée = une ligne + un arrêt + un sens)."""

    VERSION = 1

    def __init__(self) -> None:
        self._client: TbmClient | None = None
        self._lines: dict[str, Line] = {}
        self._line: Line | None = None
        self._choices: dict[str, StopChoice] = {}

    @property
    def client(self) -> TbmClient:
        if self._client is None:
            self._client = TbmClient(async_get_clientsession(self.hass))
        return self._client

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Étape 1 : choix de la ligne."""
        errors: dict[str, str] = {}

        if user_input is not None and user_input.get(CONF_LINE_REF) in self._lines:
            self._line = self._lines[user_input[CONF_LINE_REF]]
            return await self.async_step_stop()

        if not self._lines:
            try:
                lines = await self.client.async_get_lines()
            except TbmConnectionError:
                errors["base"] = "cannot_connect"
                lines = []
            except TbmError:
                _LOGGER.exception("Lecture des lignes impossible")
                errors["base"] = "no_lines"
                lines = []
            self._lines = {line.ref: line for line in lines}

        if not self._lines:
            return self.async_show_form(
                step_id="user", data_schema=vol.Schema({}), errors=errors
            )

        options = [
            SelectOptionDict(value=line.ref, label=line.label)
            for line in self._lines.values()
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_LINE_REF): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        mode=SelectSelectorMode.DROPDOWN,
                        sort=False,
                    )
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Étape 2 : choix de l'arrêt et du sens, pour la ligne choisie."""
        assert self._line is not None
        errors: dict[str, str] = {}

        if user_input is not None and user_input.get(CONF_CHOICE) in self._choices:
            choice = self._choices[user_input[CONF_CHOICE]]
            line = self._line
            await self.async_set_unique_id(
                f"{line.ref}|{choice.stop_ref}|{choice.direction_ref}"
            )
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"{line.label} · {choice.stop_name} → {choice.direction_name}",
                data={
                    CONF_LINE_REF: line.ref,
                    CONF_LINE_NAME: line.label,
                    CONF_LINE_MODE: line.mode,
                    CONF_STOP_REF: choice.stop_ref,
                    CONF_STOP_NAME: choice.stop_name,
                    CONF_DIRECTION_REF: choice.direction_ref,
                    CONF_DIRECTION_NAME: choice.direction_name,
                    CONF_DESTINATION_REF: choice.destination_ref,
                    CONF_PHYSICAL: choice.physical,
                },
            )

        if not self._choices:
            try:
                choices = await self.client.async_get_stop_choices(self._line)
            except TbmConnectionError:
                errors["base"] = "cannot_connect"
                choices = []
            except TbmError:
                _LOGGER.exception("Lecture des arrêts impossible")
                errors["base"] = "no_stops"
                choices = []
            self._choices = {choice.key: choice for choice in choices}

        placeholders = {"line": self._line.label}
        if not self._choices:
            return self.async_show_form(
                step_id="stop",
                data_schema=vol.Schema({}),
                errors=errors,
                description_placeholders=placeholders,
            )

        options = [
            SelectOptionDict(value=choice.key, label=choice.label)
            for choice in self._choices.values()
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_CHOICE): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        mode=SelectSelectorMode.DROPDOWN,
                        sort=False,
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="stop",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )
