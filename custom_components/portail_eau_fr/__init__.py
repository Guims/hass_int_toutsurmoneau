"""Intégration Tout sur mon eau avec limitation des requêtes."""
from __future__ import annotations

import logging
from datetime import date, timedelta

import toutsurmoneau
from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import DOMAIN, VERSION

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]
ONE_TIME_RETRY_VERSION = "0.0.27"


class SafeSuezClient:
    """Coupe les appels à Suez après toute erreur, même après redémarrage HA.

    Le verrou persiste dans ConfigEntry.data. Une réinitialisation du mot de
    passe nécessite de reconfigurer explicitement l'intégration.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._last_known_reading_date: date | None = None
        self.set_last_known_reading_date(entry.data.get("last_suez_returned_reading_date"))
        self._client = toutsurmoneau.AsyncClient(
            username=entry.data["username"],
            password=entry.data["password"],
            meter_id=entry.data["meter_id"],
            url=entry.data["url"],
            session=async_get_clientsession(hass),
        )

    def _new_meter_client(self):
        """Client neuf pour la relève quotidienne, sans boucle de connexion.

        L'ancien client gardait un verrou d'authentification après la première
        connexion, ce qui empêchait de se reconnecter le lendemain.
        """
        return toutsurmoneau.AsyncClient(
            username=self._entry.data["username"],
            password=self._entry.data["password"],
            meter_id=self._entry.data["meter_id"],
            url=self._entry.data["url"],
            session=async_get_clientsession(self._hass),
        )

    @property
    def meter_id(self) -> str:
        """Identifiant du compteur."""
        return self._entry.data["meter_id"]

    async def _call(self, method: str):
        """Un seul appel demandé, sans blocage automatique de 24 h."""
        return await getattr(self._client, method)()

    async def async_contracts(self):
        return await self._call("async_contracts")

    @property
    def blocked_until(self) -> None:
        """Le blocage de 24 h a été désactivé."""
        return None

    def _can_use_one_time_retry(self) -> bool:
        """Un seul essai exceptionnel pour cette mise à jour, même après redémarrage."""
        return (
            not self._readings_up_to_date()
            and self._entry.data.get("suez_immediate_retry_version") != ONE_TIME_RETRY_VERSION
        )

    @property
    def last_request_time(self) -> str | None:
        """Date et heure de la dernière tentative Suez enregistrée."""
        return self._entry.data.get("last_suez_request_time")

    @property
    def last_request_result(self) -> str | None:
        """Dernier résultat observable, sans détails HTTP sensibles."""
        return self._entry.data.get("last_suez_request_result")

    @staticmethod
    def _error_reason(error: Exception) -> str:
        """Diagnostic limité à des catégories, sans URL ou données privées."""
        message = str(error).lower()
        if "session expired" in message or "authentication previously failed" in message:
            return "session_expiree"
        if "authentication required" in message:
            return "authentification_requise"
        if "authentication failed" in message or "authentication rejected" in message:
            return "authentification_refusee"
        if "api rejected" in message or "api returned an error" in message:
            return "reponse_api_refusee"
        if "unexpected api content type" in message:
            return "reponse_non_json"
        if "http error" in message:
            return "erreur_http"
        return "autre_erreur"

    def _save_request_stage(self, stage: str, error_type: str | None = None, error_reason: str | None = None) -> None:
        """Conserver le stade atteint, sans secret ni requête additionnelle."""
        self._hass.config_entries.async_update_entry(
            self._entry,
            data={
                **self._entry.data,
                "last_suez_request_result": stage,
                "last_suez_request_stage_time": dt_util.now().isoformat(),
                "last_suez_error_type": error_type,
                "last_suez_error_reason": error_reason,
            },
        )
        _LOGGER.info("Relevé Suez : étape %s", stage)

    def set_last_known_reading_date(self, reading_date: str | None) -> None:
        """Enregistrer la date du capteur restauré sans appel réseau."""
        if not reading_date:
            return
        try:
            parsed = date.fromisoformat(str(reading_date)[:10])
        except (TypeError, ValueError):
            return
        if parsed <= dt_util.now().date() and (
            self._last_known_reading_date is None
            or parsed > self._last_known_reading_date
        ):
            self._last_known_reading_date = parsed

    @property
    def expected_reading_date(self) -> date:
        """Suez publie la veille vers 16 h, selon le rythme observé."""
        now = dt_util.now()
        days_back = 1 if now.hour >= 16 else 2
        return now.date() - timedelta(days=days_back)

    def _readings_up_to_date(self) -> bool:
        return (
            self._last_known_reading_date is not None
            and self._last_known_reading_date >= self.expected_reading_date
        )

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
        if self._readings_up_to_date():
            reason = "releve_deja_a_jour"
        elif self._can_use_one_time_retry():
            reason = "essai_exceptionnel_v027"
        elif self._entry.data.get(key) == today:
            reason = "creneau_deja_tente"
        elif slot == "startup" and self._entry.data.get("last_suez_request_date") == today:
            reason = "ancienne_limite_journaliere"
        else:
            reason = "autorise"
        return {
            "suez_creneau_actuel": slot,
            "suez_date_releve_attendue": self.expected_reading_date.isoformat(),
            "suez_date_releve_connue": self._last_known_reading_date.isoformat() if self._last_known_reading_date else None,
            "suez_decision_releve": reason,
            "suez_date_tentative_avant_16h": self._entry.data.get("last_suez_startup_request_date"),
            "suez_date_tentative_apres_16h": self._entry.data.get("last_suez_scheduled_request_date"),
            "suez_ancienne_date_tentative": self._entry.data.get("last_suez_request_date"),
            "suez_etape_derniere_tentative": self._entry.data.get("last_suez_request_result") or "inconnue",
            "suez_heure_derniere_etape": self._entry.data.get("last_suez_request_stage_time"),
            "suez_type_derniere_erreur": self._entry.data.get("last_suez_error_type"),
            "suez_cause_derniere_erreur": self._entry.data.get("last_suez_error_reason"),
            "suez_date_releve_retournee": self._entry.data.get("last_suez_returned_reading_date"),
        }

    def should_refresh_today(self) -> bool:
        """Date du relevé d'abord, puis limite par créneau ou essai exceptionnel."""
        if self._readings_up_to_date():
            return False
        if self._can_use_one_time_retry():
            return True
        today = dt_util.now().date().isoformat()
        slot = self._request_slot()
        key = (
            "last_suez_startup_request_date"
            if slot == "startup"
            else "last_suez_scheduled_request_date"
        )
        if self._entry.data.get(key) == today:
            return False
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
        emergency = self._can_use_one_time_retry()
        self._hass.config_entries.async_update_entry(
            self._entry, data={
                **self._entry.data,
                key: today,
                **({"suez_immediate_retry_version": ONE_TIME_RETRY_VERSION} if emergency else {}),
                "last_suez_request_time": dt_util.now().isoformat(),
                "last_suez_request_result": "reserve",
                "last_suez_request_stage_time": dt_util.now().isoformat(),
                "last_suez_error_type": None,
                "last_suez_error_reason": None,
            }
        )

    def _record_published_date(self, value: str) -> None:
        """Mémoriser la date du relevé réellement retourné par Suez."""
        self._hass.config_entries.async_update_entry(
            self._entry,
            data={**self._entry.data, "last_suez_returned_reading_date": value},
        )
        self.set_last_known_reading_date(value)
        self._save_request_stage("reussi")

    async def async_latest_meter_reading(self):
        """Lire au maximum deux mois, sans répétition cachée en cas d'erreur."""
        self._check_and_mark_daily_request()
        today = dt_util.now().date()
        try:
            # Instance neuve par relève autorisée : une nouvelle session peut
            # être ouverte si les cookies Suez ont expiré depuis la veille.
            client = self._new_meter_client()
            self._save_request_stage("appel_lance")
            monthly = await client.async_daily_for_month(today)
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
            monthly = await client.async_daily_for_month(previous_month)
            self._save_request_stage("reponse_recue")
            readings = [
                (day, value) for day, value in monthly["absolute"].items()
                if value is not None
            ]
            if readings:
                day, index = max(readings)
                published_date = previous_month.replace(day=day).isoformat()
                self._record_published_date(published_date)
                return {
                    "volume": index,
                    "daily_volume": monthly["daily"].get(day),
                    "reading_date": published_date,
                }
            raise ValueError("Aucun index de compteur disponible")
        except Exception as err:
            # Le nom de la classe d'exception est suffisant pour diagnostiquer,
            # sans enregistrer URL, identifiant, mot de passe ou réponse brute.
            self._save_request_stage("echec", type(err).__name__, self._error_reason(err))
            _LOGGER.warning("Échec de relève Suez ; pas de nouvel essai automatique dans ce créneau")
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
