"""Intégration Tout sur mon eau avec limitation des requêtes."""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import toutsurmoneau
from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, VERSION

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]
COOLDOWN_SECONDS = int(timedelta(hours=24).total_seconds())


class SafeSuezClient:
    """Coupe les appels à Suez après toute erreur, même après redémarrage HA.

    Le verrou persiste dans ConfigEntry.data. Une réinitialisation du mot de
    passe nécessite de reconfigurer explicitement l'intégration.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._client = toutsurmoneau.AsyncClient(
            username=entry.data["username"],
            password=entry.data["password"],
            meter_id=entry.data["meter_id"],
            url=entry.data["url"],
            session=async_get_clientsession(hass),
        )

    @property
    def meter_id(self) -> str:
        """Identifiant du compteur."""
        return self._entry.data["meter_id"]

    def _check_cooldown(self) -> None:
        blocked_until = float(self._entry.data.get("blocked_until", 0))
        if time.time() < blocked_until:
            raise RuntimeError("Requêtes Suez suspendues pour 24 h après erreur")

    def _block(self) -> None:
        blocked_until = time.time() + COOLDOWN_SECONDS
        self._hass.config_entries.async_update_entry(
            self._entry, data={**self._entry.data, "blocked_until": blocked_until}
        )
        _LOGGER.warning(
            "Communication Suez interrompue après une erreur. Aucun nouvel essai "
            "automatique avant 24 h. Vérifiez le compte sur le site officiel."
        )

    async def _call(self, method: str):
        self._check_cooldown()
        try:
            return await getattr(self._client, method)()
        except Exception:
            self._block()
            raise

    async def async_contracts(self):
        """Liste des contrats Suez."""
        return await self._call("async_contracts")

    async def async_latest_meter_reading(self):
        """Lire au maximum deux mois, sans répétition cachée en cas d'erreur."""
        self._check_cooldown()
        today = date.today()
        try:
            monthly = await self._client.async_daily_for_month(today)
            readings = [
                (day, value) for day, value in monthly["absolute"].items()
                if day <= today.day and value is not None
            ]
            if readings:
                day, index = max(readings)
                return {
                    "volume": index,
                    "daily_volume": monthly["daily"].get(day),
                    "reading_date": today.replace(day=day).isoformat(),
                }
            # Les premiers jours du mois, le dernier index peut dater du mois précédent.
            previous_month = today.replace(day=1) - timedelta(days=1)
            monthly = await self._client.async_daily_for_month(previous_month)
            readings = [
                (day, value) for day, value in monthly["absolute"].items()
                if value is not None
            ]
            if readings:
                day, index = max(readings)
                return {
                    "volume": index,
                    "daily_volume": monthly["daily"].get(day),
                    "reading_date": previous_month.replace(day=day).isoformat(),
                }
            raise ValueError("Aucun index de compteur disponible")
        except Exception:
            self._block()
            raise


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Charger une configuration sans nouvelle connexion de vérification."""
    _LOGGER.debug("Démarrage Tout sur mon eau, version %s", VERSION)
    client = SafeSuezClient(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception as err:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise ConfigEntryNotReady("Impossible d'initialiser le capteur Suez") from err
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Décharger l'intégration."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
