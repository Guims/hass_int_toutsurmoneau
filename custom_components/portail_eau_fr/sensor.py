"""Capteur Suez : rattrapage au démarrage et relève à 16 h."""
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
from homeassistant.helpers.event import async_call_later, async_track_time_change
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

        self._set_diagnostic_attributes()

        self.async_on_remove(
            async_track_time_change(
                self.hass, self._scheduled_refresh, hour=16, minute=0, second=0
            )
        )

        # Avant 16 h : une relève au démarrage (sans doublon après redémarrage).
        # À 16 h : nouvelle relève autorisée, même après celle du matin.
        # Après 16 h : rattrapage si le créneau de 16 h a été manqué.
        self.async_on_remove(
            async_call_later(self.hass, 3, self._startup_refresh)
        )

    @callback
    def _startup_refresh(self, now: datetime) -> None:
        """Exécuter le rattrapage après l'ajout complet du capteur."""
        self._set_diagnostic_attributes()
        self.async_write_ha_state()
        if self._api.should_refresh_today():
            _LOGGER.info("Rattrapage du relevé Suez au démarrage")
            self.async_schedule_update_ha_state(force_refresh=True)
        else:
            _LOGGER.info(
                "Rattrapage Suez ignoré : %s",
                self._api.request_diagnostics["suez_decision_releve"],
            )

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
        """Relevé de 16 h, indépendant de celui du matin."""
        self._set_diagnostic_attributes()
        self.async_write_ha_state()
        if self._api.should_refresh_today():
            self.async_schedule_update_ha_state(force_refresh=True)
        else:
            _LOGGER.info(
                "Relevé Suez de 16 h ignoré : %s",
                self._api.request_diagnostics["suez_decision_releve"],
            )

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
            self._set_diagnostic_attributes()
        except Exception:
            # La période de blocage est gérée et persistée par SafeSuezClient.
            # Pas de traceback : certains détails HTTP peuvent être sensibles.
            self._set_diagnostic_attributes()
            _LOGGER.warning("Relevé Suez non actualisé ; dernière valeur conservée")
