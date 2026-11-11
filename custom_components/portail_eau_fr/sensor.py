"""Capteur de compteur Suez, une actualisation par jour."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(hours=24)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Créer le capteur ; aucun appel réseau n'est déclenché ici."""
    api = hass.data[DOMAIN][entry.entry_id]
    # Premier relevé dès l'ajout, puis actualisation automatique toutes les 24 h.
    # Le délai de sécurité persistant empêche les essais répétés après une erreur.
    async_add_entities([ToutSurMonEauEntity(api)], update_before_add=True)


class ToutSurMonEauEntity(SensorEntity):
    """Index absolu du compteur d'eau."""

    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_icon = "mdi:water-pump"
    _attr_should_poll = True

    def __init__(self, api) -> None:
        self._api = api
        # Conserver le même unique_id que le projet initial.
        self._attr_unique_id = "unique_id_with_meter"
        self._attr_name = f"Suez meter {api.meter_id}"
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}

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
