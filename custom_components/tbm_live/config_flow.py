"""Assistant de configuration : ligne, puis arrêt, puis sens."""

from __future__ import annotations

import logging
from typing import Any

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
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import Line, LineStop, StopDirection, TbmClient, TbmConnectionError, TbmError
from .const import (
    CONF_COUNT,
    CONF_DIRECTION,
    CONF_DIRECTION_NAME,
    CONF_DIRECTION_REF,
    CONF_LINE,
    CONF_LINE_CODE,
    CONF_LINE_MODE,
    CONF_LINE_NAME,
    CONF_LINE_REF,
    CONF_STOP,
    CONF_STOP_NAME,
    CONF_STOP_REF,
    CONF_STOP_REFS,
    DEFAULT_COUNT,
    DOMAIN,
    MAX_COUNT,
    MIN_COUNT,
)

_LOGGER = logging.getLogger(__name__)


def _unique_labels(items: list[tuple[str, Any]]) -> dict[str, Any]:
    """Associe un libellé unique à chaque élément (suffixe en cas de doublon)."""
    result: dict[str, Any] = {}
    for label, item in items:
        candidate, n = label, 2
        while candidate in result:
            candidate, n = f"{label} ({n})", n + 1
        result[candidate] = item
    return result


def _count_selector() -> NumberSelector:
    """Curseur 2 → 5 pour le nombre de passages."""
    return NumberSelector(
        NumberSelectorConfig(
            min=MIN_COUNT, max=MAX_COUNT, step=1, mode=NumberSelectorMode.SLIDER
        )
    )


def _search_select(labels: list[str]) -> SelectSelector:
    """Liste déroulante avec recherche au clavier.

    `custom_value` fait afficher un champ de saisie qui filtre la liste au fil
    de la frappe. La valeur est le libellé lui-même : le champ affiche donc
    toujours un texte lisible. Une saisie libre est refusée par l'assistant.
    """
    return SelectSelector(
        SelectSelectorConfig(
            options=labels,
            mode=SelectSelectorMode.DROPDOWN,
            custom_value=True,
            sort=False,
        )
    )


class TbmConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ajout d'un arrêt suivi (une entrée = une ligne + un arrêt + un sens)."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TbmOptionsFlow:
        """Menu « Configurer » d'une entrée."""
        return TbmOptionsFlow()

    def __init__(self) -> None:
        self._client: TbmClient | None = None
        self._lines: dict[str, Line] = {}
        self._line: Line | None = None
        self._stops: dict[str, LineStop] = {}
        self._stop: LineStop | None = None
        self._directions: dict[str, StopDirection] = {}

    @property
    def client(self) -> TbmClient:
        if self._client is None:
            self._client = TbmClient(async_get_clientsession(self.hass))
        return self._client

    # ------------------------------------------------------------------ ligne

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Étape 1 : choix de la ligne."""
        errors: dict[str, str] = {}

        if user_input is not None and self._lines:
            line = self._lines.get(user_input.get(CONF_LINE, ""))
            if line is not None:
                self._line = line
                self._stops = {}
                return await self.async_step_stop()
            errors[CONF_LINE] = "invalid_choice"

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
            self._lines = _unique_labels([(line.label, line) for line in lines])

        if not self._lines:
            return self.async_show_form(
                step_id="user", data_schema=vol.Schema({}), errors=errors
            )

        schema = vol.Schema(
            {vol.Required(CONF_LINE): _search_select(list(self._lines))}
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # ------------------------------------------------------------------ arrêt

    async def async_step_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Étape 2 : choix de l'arrêt sur la ligne choisie."""
        assert self._line is not None
        errors: dict[str, str] = {}

        if user_input is not None and self._stops:
            stop = self._stops.get(user_input.get(CONF_STOP, ""))
            if stop is not None:
                self._stop = stop
                self._directions = _unique_labels(
                    [(d.label, d) for d in stop.directions.values()]
                )
                return await self.async_step_direction()
            errors[CONF_STOP] = "invalid_choice"

        if not self._stops:
            try:
                stops = await self.client.async_get_line_stops(self._line)
            except TbmConnectionError:
                errors["base"] = "cannot_connect"
                stops = []
            except TbmError:
                _LOGGER.exception("Lecture des arrêts impossible")
                errors["base"] = "no_stops"
                stops = []
            self._stops = _unique_labels(
                [(stop.name, stop) for stop in stops if stop.directions]
            )

        placeholders = {"line": self._line.label}
        if not self._stops:
            return self.async_show_form(
                step_id="stop",
                data_schema=vol.Schema({}),
                errors=errors,
                description_placeholders=placeholders,
            )

        schema = vol.Schema(
            {vol.Required(CONF_STOP): _search_select(list(self._stops))}
        )
        return self.async_show_form(
            step_id="stop",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    # ------------------------------------------------------------------- sens

    async def async_step_direction(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Étape 3 : choix du sens (terminus) à cet arrêt."""
        assert self._line is not None and self._stop is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            direction = self._directions.get(user_input.get(CONF_DIRECTION, ""))
            if direction is not None:
                return await self._create(
                    direction, int(user_input.get(CONF_COUNT, DEFAULT_COUNT))
                )
            errors[CONF_DIRECTION] = "invalid_choice"

        schema = vol.Schema(
            {
                vol.Required(CONF_DIRECTION): SelectSelector(
                    SelectSelectorConfig(
                        options=list(self._directions),
                        mode=SelectSelectorMode.LIST,
                        sort=False,
                    )
                ),
                vol.Required(CONF_COUNT, default=DEFAULT_COUNT): _count_selector(),
            }
        )
        return self.async_show_form(
            step_id="direction",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "line": self._line.label,
                "stop": self._stop.name,
            },
        )

    # ---------------------------------------------------------------- création

    async def _create(self, direction: StopDirection, count: int) -> ConfigFlowResult:
        assert self._line is not None and self._stop is not None
        line, stop = self._line, self._stop
        # Même format d'identifiant qu'en v0.1 : les doublons restent détectés.
        unique_id = f"{line.ref}|{direction.stop_refs[0]}|{direction.ref}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()
        terminus = " / ".join(direction.destinations) or direction.label
        return self.async_create_entry(
            title=f"{line.label} · {stop.name} → {terminus}",
            data={
                CONF_LINE_REF: line.ref,
                CONF_LINE_NAME: line.label,
                CONF_LINE_MODE: line.mode,
                CONF_LINE_CODE: line.short_name,
                CONF_STOP_NAME: stop.name,
                CONF_STOP_REF: direction.stop_refs[0],
                CONF_STOP_REFS: direction.stop_refs,
                CONF_DIRECTION_REF: direction.ref,
                CONF_DIRECTION_NAME: terminus,
            },
            options={CONF_COUNT: count},
        )


class TbmOptionsFlow(OptionsFlow):
    """Réglages modifiables après coup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={CONF_COUNT: int(user_input[CONF_COUNT])})

        current = int(self.config_entry.options.get(CONF_COUNT, DEFAULT_COUNT))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_COUNT, default=current): _count_selector()}
            ),
        )
