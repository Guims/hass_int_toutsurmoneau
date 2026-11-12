"""Intégration Tout sur mon eau avec limitation des requêtes."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

import toutsurmoneau
from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

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

    @property
    def blocked_until(self) -> str | None:
        """Heure de fin du blocage de sécurité, si actif."""
        timestamp = float(self._entry.data.get("blocked_until", 0))
        if time.time() >= timestamp:
            return None
        return dt_util.as_local(
            datetime.fromtimestamp(timestamp, tz=timezone.utc)
        ).isoformat()

    @property
    def last_request_time(self) -> str | None:
        """Date et heure de la dernière tentative Suez enregistrée."""
        return self._entry.data.get("last_suez_request_time")

    @property
    def last_request_result(self) -> str | None:
        """Dernier résultat observable, sans détails HTTP sensibles."""
        return self._entry.data.get("last_suez_request_result")

    def _save_request_stage(self, stage: str, error_type: str | None = None) -> None:
        """Conserver le stade atteint, sans secret ni requête additionnelle."""
        self._hass.config_entries.async_update_entry(
            self._entry,
            data={
                **self._entry.data,
                "last_suez_request_result": stage,
                "last_suez_request_stage_time": dt_util.now().isoformat(),
                "last_suez_error_type": error_type,
            },
        )
        _LOGGER.info("Relevé Suez : étape %s", stage)

    def _request_slot(self) -> str:
        """Séparer le rattrapage avant 16 h du rendez-vous de 16 h."""
        return "startup" if dt_util.now().hour < 16 else "scheduled"

    @property
    def request_diagnostics(self) -> dict:
        """Informations locales expliquant les décisions de relève."""
        now = dt_util.now()
        today = now.date().isoformat()
        slot = self._request_slot()
        key = (
            "last_suez_startup_request_date"
            if slot == "startup"
            else "last_suez_scheduled_request_date"
        )
        if time.time() < float(self._entry.data.get("blocked_until", 0)):
            reason = "suspension_24h"
        elif self._entry.data.get(key) == today:
            reason = "creneau_deja_tente"
        elif slot == "startup" and self._entry.data.get("last_suez_request_date") == today:
            reason = "ancienne_limite_journaliere"
        else:
            reason = "autorise"
        return {
            "suez_creneau_actuel": slot,
            "suez_decision_releve": reason,
            "suez_date_tentative_avant_16h": self._entry.data.get("last_suez_startup_request_date"),
            "suez_date_tentative_apres_16h": self._entry.data.get("last_suez_scheduled_request_date"),
            "suez_ancienne_date_tentative": self._entry.data.get("last_suez_request_date"),
            "suez_etape_derniere_tentative": self._entry.data.get("last_suez_request_result") or "inconnue",
            "suez_heure_derniere_etape": self._entry.data.get("last_suez_request_stage_time"),
            "suez_type_derniere_erreur": self._entry.data.get("last_suez_error_type"),
            "suez_date_releve_retournee": self._entry.data.get("last_suez_returned_reading_date"),
        }

    def should_refresh_today(self) -> bool:
        """Autoriser une relève matinale ET celle de 16 h, sans doublons."""
        now = dt_util.now()
        today = now.date().isoformat()
        if time.time() < float(self._entry.data.get("blocked_until", 0)):
            return False
        slot = self._request_slot()
        key = (
            "last_suez_startup_request_date"
            if slot == "startup"
            else "last_suez_scheduled_request_date"
        )
        if self._entry.data.get(key) == today:
            return False
        # Compatibilité avec la version 0.0.19 : une tentative déjà enregistrée
        # aujourd'hui ne doit pas être répétée avant 16 h.
        if slot == "startup" and self._entry.data.get("last_suez_request_date") == today:
            return False
        return True

    def _check_and_mark_daily_request(self) -> None:
        """Réserver durablement le créneau avant tout accès à Suez."""
        if not self.should_refresh_today():
            raise RuntimeError("Relevé déjà tenté pour ce créneau ou temporairement suspendu")
        slot = self._request_slot()
        key = (
            "last_suez_startup_request_date"
            if slot == "startup"
            else "last_suez_scheduled_request_date"
        )
        today = dt_util.now().date().isoformat()
        self._hass.config_entries.async_update_entry(
            self._entry, data={
                **self._entry.data,
                key: today,
                "last_suez_request_time": dt_util.now().isoformat(),
                "last_suez_request_result": "reserve",
                "last_suez_request_stage_time": dt_util.now().isoformat(),
                "last_suez_error_type": None,
            }
        )

    def _record_published_date(self, value: str) -> None:
        """Mémoriser la date du relevé réellement retourné par Suez."""
        self._hass.config_entries.async_update_entry(
            self._entry,
            data={**self._entry.data, "last_suez_returned_reading_date": value},
        )
        self._save_request_stage("reussi")

    async def async_latest_meter_reading(self):
        """Lire au maximum deux mois, sans répétition cachée en cas d'erreur."""
        self._check_cooldown()
        self._check_and_mark_daily_request()
        today = dt_util.now().date()
        try:
            # L'appel commence ici ; la réservation seule n'est pas une réponse Suez.
            self._save_request_stage("appel_lance")
            monthly = await self._client.async_daily_for_month(today)
            self._save_request_stage("reponse_recue")
            readings = [
                (day, value) for day, value in monthly["absolute"].items()
                if day <= today.day and value is not None
            ]
            if readings:
                day, index = max(readings)
                published_date = today.replace(day=day).isoformat()
                self._record_published_date(published_date)
                return {
                    "volume": index,
                    "daily_volume": monthly["daily"].get(day),
                    "reading_date": published_date,
                }
            # Les premiers jours du mois, le dernier index peut dater du mois précédent.
            previous_month = today.replace(day=1) - timedelta(days=1)
            self._save_request_stage("appel_mois_precedent")
            monthly = await self._client.async_daily_for_month(previous_month)
            self._save_request_stage("reponse_recue")
            readings = [
                (day, value) for day, value in monthly["absolute"].items()
                if value is not None
            ]
            if readings:
                self._save_request_result("reussi")
                day, index = max(readings)
                return {
                    "volume": index,
                    "daily_volume": monthly["daily"].get(day),
                    "reading_date": previous_month.replace(day=day).isoformat(),
                }
            raise ValueError("Aucun index de compteur disponible")
        except Exception as err:
            # Le nom de la classe d'exception est suffisant pour diagnostiquer,
            # sans enregistrer URL, identifiant, mot de passe ou réponse brute.
            self._save_request_stage("echec", type(err).__name__)
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
