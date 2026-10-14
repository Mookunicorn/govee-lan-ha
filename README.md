# Govee LAN pour Home Assistant

Intégration personnalisée (HACS) qui pilote vos lumières Govee **en local**, via l'[API LAN officielle de Govee](https://app-h5.govee.com/user-manual/wlan-guide) (JSON sur UDP). Aucun compte Govee, aucune clé API, aucun cloud.

## Fonctionnalités

- Découverte automatique par multicast, sur toutes les interfaces réseau de Home Assistant
- Allumer / éteindre, luminosité, couleur RGB et température de couleur (2000 K – 9000 K)
- Appareils ajoutés automatiquement quand ils apparaissent sur le réseau
- Saisie manuelle d'adresses IP si le multicast est bloqué (VLAN, Wi-Fi invités…)
- Mise à jour de l'adresse IP si l'appareil change (DHCP)
- Interface en français et en anglais

## Prérequis

1. Un appareil Govee **compatible API LAN** (voir la liste sur la [page officielle](https://app-h5.govee.com/user-manual/wlan-guide)). Tous les modèles ne la supportent pas.
2. Dans l'application **Govee Home** : ouvrez l'appareil → Paramètres → activez **Contrôle LAN** (si le réglage n'apparaît pas, mettez le firmware à jour et patientez, Govee indique que cela peut prendre jusqu'à 30 minutes).
3. Home Assistant et les lumières sur le même réseau local, multicast autorisé (désactivez « IGMP snooping » ou « isolation des clients » si besoin).
4. Le port UDP **4002** de la machine Home Assistant doit être libre.

## Installation via HACS

1. HACS → menu ⋮ → **Dépôts personnalisés**.
2. Ajoutez l'URL de ce dépôt GitHub, catégorie **Intégration**.
3. Cherchez **Govee LAN** dans HACS, installez, puis **redémarrez Home Assistant**.
4. Paramètres → Appareils et services → **Ajouter une intégration** → *Govee LAN*.

### Installation manuelle

Copiez le dossier `custom_components/govee_lan` dans le dossier `config/custom_components/` de Home Assistant, puis redémarrez.

## Configuration

L'assistant scanne le réseau pendant quelques secondes. Il crée l'entrée s'il trouve au moins un appareil.

- **Adresses IP manuelles** : liste séparée par des virgules, interrogées en unicast en plus du multicast.
- **Options** (Configurer sur la carte de l'intégration) : adresses IP manuelles et intervalle d'actualisation (10 à 3600 s, 30 s par défaut).

Une seule instance de l'intégration est possible : l'API Govee impose d'écouter sur le port 4002.

## Dépannage

| Symptôme | Piste |
|---|---|
| « Aucun appareil trouvé » | « Contrôle LAN » activé dans l'app ? Appareil allumé ? Essayez de saisir son IP manuellement. |
| « Port 4002 déjà utilisé » | Autre intégration Govee, govee2mqtt, etc. Un seul programme peut écouter sur ce port. |
| Home Assistant dans Docker | Utilisez `network_mode: host`. Sans cela le multicast n'arrive pas au conteneur. |
| Entité « indisponible » | L'appareil ne répond plus à `devStatus` (éteint au secteur, Wi-Fi, LAN désactivé). |
| Changement de couleur lent | Le firmware Govee applique un fondu que l'API LAN ne permet pas de raccourcir. |

Pour les logs détaillés :

```yaml
logger:
  logs:
    custom_components.govee_lan: debug
```

## Protocole utilisé

| Usage | Direction | Port |
|---|---|---|
| Découverte (`scan`) | client → `239.255.255.250` | 4001 |
| Réponse au scan et à `devStatus` | appareil → client | 4002 |
| Commandes `turn`, `brightness`, `colorwc`, `devStatus` | client → appareil | 4003 |

> Le protocole LAN de Govee n'est ni chiffré ni authentifié : toute machine du réseau local peut piloter ces lumières. N'activez « Contrôle LAN » que sur un réseau de confiance.

## Développement

Le client UDP (`protocol.py`) ne dépend pas de Home Assistant et se teste contre un faux appareil :

```bash
python -m unittest discover -s tests -v
```

Avant de publier, remplacez `OWNER` dans `custom_components/govee_lan/manifest.json` par votre nom d'utilisateur GitHub, ajoutez-le dans `codeowners` (`["@votre-nom"]`), puis créez une release GitHub (par exemple `v1.0.0`) pour que HACS propose une version.

## Licence

MIT
