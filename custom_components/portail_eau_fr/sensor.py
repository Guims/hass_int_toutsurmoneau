"""Capteur Suez : rattrapage au démarrage et relève à 16 h."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.core import callback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Créer le capteur et permettre le rattrapage au démarrage."""
    api = hass.data[DOMAIN][entry.entry_id]
    # Restaurer l'index, rattraper si nécessaire puis suivre le rendez-vous de 16 h.
    async_add_entities([ToutSurMonEauEntity(api)], update_before_add=False)


class ToutSurMonEauEntity(RestoreEntity, SensorEntity):
    """Index absolu du compteur d'eau."""

    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_icon = "mdi:water-pump"
    _attr_should_poll = False

    def __init__(self, api) -> None:
        self._api = api
        self._refresh_lock = asyncio.Lock()
        # Conserver le même unique_id que le projet initial.
        self._attr_unique_id = "unique_id_with_meter"
        self._attr_name = f"Suez meter {api.meter_id}"
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        """Restaurer l'index, planifier 16 h et rattraper si nécessaire."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                self._attr_native_value = float(last_state.state)
            except (TypeError, ValueError):
                pass
            self._attr_extra_state_attributes = {
                key: last_state.attributes[key]
                for key in ("derniere_consommation_jour_l", "date_derniere_consommation")
                if key in last_state.attributes
            }

        self._api.set_last_known_reading_date(
            self._attr_extra_state_attributes.get("date_derniere_consommation")
        )
        self._set_diagnostic_attributes()

        self.async_on_remove(
            async_track_time_change(
                self.hass, self._scheduled_refresh, hour=16, minute=0, second=0
            )
        )

        # Rattrapage immédiat lors de l'ajout de l'entité, sans timer.
        # Les attributs seront publiés par Home Assistant après l'ajout.
        if self._api.should_refresh_today():
            _LOGGER.info("Rattrapage Suez au démarrage : récupération lancée")
            await self._refresh_from_suez()
        else:
            _LOGGER.info(
                "Rattrapage Suez ignoré : %s",
                self._api.request_diagnostics["suez_decision_releve"],
            )
        self._set_diagnostic_attributes()

    async def _refresh_from_suez(self) -> None:
        """Sérialiser les relevés au démarrage et à 16 h."""
        async with self._refresh_lock:
            await self.async_update()

    def _set_diagnostic_attributes(self) -> None:
        """Exposer les dernières tentatives sans effectuer de requête HTTP."""
        self._attr_extra_state_attributes.update({
            **self._api.request_diagnostics,
            "derniere_tentative_suez": self._api.last_request_time,
            "resultat_derniere_tentative_suez": self._api.last_request_result or "aucune",
            "suez_suspendu_jusquau": self._api.blocked_until,
        })

    @callback
    def _scheduled_refresh(self, now: datetime) -> None:
        """Déclencher le relevé de 16 h comme tâche asynchrone explicite."""
        self.hass.async_create_task(self._async_scheduled_refresh())

    async def _async_scheduled_refresh(self) -> None:
        """Collecter directement sans dépendre du polling de l'entité."""
        if self._api.should_refresh_today():
            _LOGGER.info("Relevé Suez de 16 h : récupération lancée")
            await self._refresh_from_suez()
        else:
            _LOGGER.info(
                "Relevé Suez ignoré : %s",
                self._api.request_diagnostics["suez_decision_releve"],
            )
        self._set_diagnostic_attributes()
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Lire la donnée ; laisser la dernière valeur si Suez est indisponible."""
        try:
            reading = await self._api.async_latest_meter_reading()
            self._attr_native_value = reading["volume"]
            # Ces attributs réutilisent la réponse API du relevé, sans requête supplémentaire.
            self._attr_extra_state_attributes = {
                "derniere_consommation_jour_l": reading["daily_volume"],
                "date_derniere_consommation": reading["reading_date"],
            }
            self._api.set_last_known_reading_date(reading["reading_date"])
            self._set_diagnostic_attributes()
        except Exception:
            # La période de blocage est gérée et persistée par SafeSuezClient.
            # Pas de traceback : certains détails HTTP peuvent être sensibles.
            self._set_diagnostic_attributes()
            _LOGGER.warning("Relevé Suez non actualisé ; dernière valeur conservée")
