# Home Assistant Integration for toutsurmoneau and others

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Version 0.0.22

Home assistant integration for Suez tout sur mon eau

## Installation

- Install HACS : <https://hacs.xyz/>
  Follow the guide: <https://hacs.xyz/docs/community_guides>

- In Home Assistant, navigate to HACS &rarr; Integrations, and then click on the menu:

  ![menu](images/menu_depot.png)

- For the address enter this github address:

  <https://github.com/Guims/hass_int_toutsurmoneau>

- Then, search: "tout"

  ![search](images/recherche.png)

- Then, click on Download:

  ![download](images/telecharger1.png)

  ![download](images/telecharger.png)

- once done, restart home assistant

  ![restart](images/redemarrer.png)

- Then, in Home Assistant, navigate to Settings &rarr; Integration and add integration, then search "tout"

- This starts the Configuration.

## Configuration

- First enter the provider URL, or select one of the provided URLs

  ![restart](images/config1.png)

- Enter username and password

- Then, the integration will discover the meter identifier, and propose it.
  (No need to inspect the web page source)

  ![restart](images/config2.png)

After that the integration is configured.

Note that the current version shows only the current meter index (absolute).
This can be used as "Water source" that can be monitored.

## To come

I plan to add the possibility to provision all available history back into Home Assistant.

## Protection du compte Suez (version 0.0.22)

- Mise à jour automatique de l'index toutes les 24 heures.
- Suppression de la vérification d'identifiants au redémarrage de Home Assistant.
- Consultation d'un mois, éventuellement du mois précédent, sans boucle de répétition sur erreur.
- Après toute erreur réseau ou Suez, suspension des appels pour 24 heures, persistante après redémarrage. Les autres erreurs (y compris une absence temporaire de relevé) déclenchent également cette suspension.
- Pour lever la suspension après changement de mot de passe, supprimer puis recréer l'entrée de configuration. Ne pas redémarrer en boucle.
- Attention : la version corrigée `toutsurmoneau` 0.0.28 est installée directement depuis le fork `Guims/py-mon-eau` (commit épinglé). Une seule authentification est autorisée par instance de client, et aucune réauthentification automatique n'est effectuée après une session expirée. Il n'est **pas garanti** que Suez ne bloque plus le compte : procéder à un essai progressif.
- **Ne jamais activer le niveau DEBUG pour `toutsurmoneau`** : la sécurité des journaux est renforcée dans le fork, mais il est préférable de laisser le niveau INFO pendant les tests.
- Si le compte est bloqué, désactiver l'intégration et contacter Suez. Ne pas recommencer la connexion en boucle.

## Développement

```bash
make bumpver
make release
```


## Dépendance corrigée

La v0.0.22 dépend du fork [Guims/py-mon-eau](https://github.com/Guims/py-mon-eau), commit `1ec88cb39148c2805cb0d3f1a1921a4ae96870bd`, et non de la version PyPI 0.0.27. Home Assistant installe la dépendance via Git ; cette installation doit pouvoir accéder à GitHub et disposer de Git. En cas d'échec, vérifier le journal d'installation des dépendances avant toute nouvelle tentative auprès de Suez.


## Dernière consommation quotidienne publiée par Suez (v0.0.22)

Le capteur d'index existant `sensor.suez_meter_3853929616` publie maintenant deux attributs :

- `derniere_consommation_jour_l` : consommation en litres pour le dernier jour avec index valide ;
- `date_derniere_consommation` : date réelle (AAAA-MM-JJ) du jour correspondant.

Ces données viennent de la **même réponse** API que l'index total : aucune requête réseau supplémentaire. L'index et son identifiant d'entité ne changent pas. Cette date est celle du relevé Suez, pas celle du jour présent.

Dans une configuration `template:` existante, ajouter un nouveau bloc au niveau des autres éléments `- sensor:` :

```yaml
- sensor:
    - name: "SUEZ dernier jour disponible"
      unique_id: suez_dernier_jour_disponible
      icon: mdi:water-check
      unit_of_measurement: "L"
      state_class: measurement
      availability: >
        {{ is_number(state_attr('sensor.suez_meter_3853929616', 'derniere_consommation_jour_l')) }}
      state: >
        {{ state_attr('sensor.suez_meter_3853929616', 'derniere_consommation_jour_l') | float(0) | round(0) }}
      attributes:
        date_releve: >
          {{ state_attr('sensor.suez_meter_3853929616', 'date_derniere_consommation') }}
```

Ne pas déclarer `device_class: water` avec `state_class: measurement`, car Home Assistant 2026.11 considère cette combinaison incompatible. Le capteur est une consommation **du dernier jour publié**, il n'est pas équivalent au compteur `utility_meter` du jour civil en cours.


## Relevé quotidien à 16 h (version 0.0.22)

Le compteur interroge Suez **chaque jour à 16:00, dans le fuseau horaire configuré dans Home Assistant** (Europe/Paris si le système est configuré en France). Il n'effectue plus de requête au redémarrage de Home Assistant. La dernière valeur d'index et ses attributs (consommation et date du relevé Suez) sont restaurés depuis le dernier état enregistré.

Pour éviter les risques de blocage du compte Suez, une limite persistante autorise **une seule tentative d'interrogation par date** à partir de 16 h, même après redémarrage ou actualisation manuelle. Le verrou existant de 24 heures en cas d'erreur reste actif. Si Home Assistant est arrêté à 16 h, la relève prévue est manquée et la prochaine aura lieu à 16 h le lendemain (aucun rattrapage automatique).

À noter : l'heure de publication annoncée par Suez ne garantit pas que la donnée soit déjà disponible à la seconde 16:00.


## Rattrapage au démarrage (v0.0.22)

En plus du rendez-vous quotidien à 16 h (fuseau horaire Home Assistant), le capteur effectue **un relevé au démarrage si aucun relevé n'a été tenté durant la date locale en cours**. Ce rattrapage peut donc être effectué avant 16 h : dans ce cas, le passage de 16 h est ignoré pour éviter un deuxième appel le même jour. Si une tentative a déjà été faite aujourd'hui, aucun nouveau relevé n'est déclenché lors du redémarrage.

La date de tentative et le verrou de sécurité de 24 heures après erreur restent persistants. Les valeurs précédentes du capteur sont restaurées en attendant la prochaine lecture autorisée. Il n'y a jamais de rafale de rattrapages pour plusieurs jours manqués.


## Deux créneaux distincts (v0.0.22)

- **Démarrage avant 16 h :** une interrogation Suez si aucune n'a déjà été tentée dans ce créneau le jour même. Plusieurs redémarrages n'entraînent pas d'autres requêtes matinales.
- **À 16 h :** une nouvelle interrogation, **même si un relevé a eu lieu à 8 h**. Une seule tentative pour ce créneau chaque jour.
- **Démarrage après 16 h :** rattrapage du relevé de 16 h s'il n'a pas encore été tenté. Aucun rattrapage multiple.
- Si Suez échoue, la suspension persistante de 24 heures reste prioritaire, ce qui peut empêcher le relevé de 16 h après une erreur matinale.
- Le dernier index et ses attributs sont restaurés après redémarrage sans déclencher de requête non autorisée.

Ce comportement fait **au maximum deux interrogations de télémétrie par jour** (une avant 16 h, une à partir de 16 h). La bibliothèque peut effectuer des échanges HTTP supplémentaires pour l'authentification, et la lecture du mois précédent peut nécessiter une seconde interrogation de télémétrie lorsque le mois courant ne contient encore aucun relevé.


## Diagnostic local de la relève (v0.0.22)

Le capteur de compteur expose deux attributs supplémentaires : `derniere_tentative_suez` et `resultat_derniere_tentative_suez` (valeurs : `aucune`, `en_cours`, `reussi`, `echec`). Ces informations sont enregistrées dans les paramètres de l'intégration et restaurées après redémarrage : **leur affichage ne crée aucune requête supplémentaire**. Une tentative réussie peut conserver le même index si Suez n'a pas encore publié de nouvelle mesure. Les limites de tentatives et la suspension de 24 heures restent inchangées.


## Rattrapage différé et visibilité de la suspension (v0.0.22)

Le rattrapage au démarrage est désormais déclenché **trois secondes après l'ajout complet du capteur** pour éviter une mise à jour trop précoce. Le créneau de 16 h reste indépendant du rattrapage effectué avant 16 h. Le capteur affiche aussi `suez_suspendu_jusquau`, avec la fin du verrou de sécurité de 24 h s'il est actif (`null` sinon). Les attributs de diagnostic ne déclenchent aucun appel supplémentaire. Les tentatives antérieures non enregistrées ne peuvent pas être reconstituées rétroactivement.
