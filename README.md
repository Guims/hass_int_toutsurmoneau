# Home Assistant Integration for toutsurmoneau and others

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Version 0.0.16

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

## Protection du compte Suez (version 0.0.16)

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

La v0.0.16 dépend du fork [Guims/py-mon-eau](https://github.com/Guims/py-mon-eau), commit `1ec88cb39148c2805cb0d3f1a1921a4ae96870bd`, et non de la version PyPI 0.0.27. Home Assistant installe la dépendance via Git ; cette installation doit pouvoir accéder à GitHub et disposer de Git. En cas d'échec, vérifier le journal d'installation des dépendances avant toute nouvelle tentative auprès de Suez.
