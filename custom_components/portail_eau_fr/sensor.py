"""Capteur de compteur Suez, une actualisation par jour."""
from __future__ import annotations

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

        self.async_on_remove(
            async_track_time_change(
                self.hass, self._scheduled_refresh, hour=16, minute=0, second=0
            )
        )

        # Une relève manquée (ou une première installation) est rattrapée
        # au démarrage, mais jamais deux fois le même jour.
        if self._api.should_refresh_today():
            self.async_schedule_update_ha_state(force_refresh=True)

    @callback
    def _scheduled_refresh(self, now: datetime) -> None:
        """Actualisation quotidienne, une fois à 16 h heure locale HA."""
        if self._api.should_refresh_today():
            self.async_schedule_update_ha_state(force_refresh=True)

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
        except Exception:
            # La période de blocage est gérée et persistée par SafeSuezClient.
            # Pas de traceback : certains détails HTTP peuvent être sensibles.
            _LOGGER.warning("Relevé Suez non actualisé ; dernière valeur conservée")
