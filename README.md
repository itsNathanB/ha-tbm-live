# TBM Live pour Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/itsNathanB/ha-tbm-live/actions/workflows/validate.yml/badge.svg)](https://github.com/itsNathanB/ha-tbm-live/actions/workflows/validate.yml)

Les prochains passages **en temps réel** des bus et trams **TBM** (Bordeaux Métropole), directement dans Home Assistant.

Choisissez une ligne, un arrêt et un sens depuis l'interface : l'intégration crée deux capteurs, **Prochain passage** et **Passage suivant**, que vous pouvez afficher sur n'importe quel tableau de bord ou utiliser dans vos automatisations.

> Projet communautaire, non affilié à TBM, Keolis ni Bordeaux Métropole.

---

## Fonctionnalités

- Configuration 100 % graphique, sans YAML ni clé d'API.
- Choix guidé en deux étapes : **ligne** (tram ou bus), puis **arrêt + sens**. La liste des arrêts dépend de la ligne choisie.
- Deux capteurs horodatés par arrêt suivi, mis à jour toutes les 60 secondes.
- Indique si l'horaire est **temps réel** ou **théorique**, et le retard en minutes.
- Les 6 prochains passages sont disponibles en attribut, pour les cartes personnalisées.
- Ajoutez autant d'arrêts que vous voulez : une entrée par couple ligne / arrêt / sens.
- Export de diagnostics intégré pour signaler un problème.

## Prérequis

- Home Assistant **2025.2** ou plus récent.
- [HACS](https://hacs.xyz) installé.
- Un accès Internet depuis Home Assistant. L'API utilisée est publique et gratuite.

## Installation

### Avec HACS (recommandé)

1. Dans Home Assistant, ouvrez **HACS**.
2. Menu **⋮** en haut à droite → **Dépôts personnalisés**.
3. Collez l'URL `https://github.com/itsNathanB/ha-tbm-live`, choisissez le type **Intégration**, puis **Ajouter**.
4. Recherchez **TBM Live** dans HACS, ouvrez-la puis cliquez sur **Télécharger**.
5. **Redémarrez Home Assistant.**

### Manuellement

1. Téléchargez la dernière [release](https://github.com/itsNathanB/ha-tbm-live/releases).
2. Copiez le dossier `custom_components/tbm_live` dans le dossier `config/custom_components/` de votre Home Assistant.
3. Redémarrez Home Assistant.

## Configuration

1. **Paramètres** → **Appareils et services** → **Ajouter une intégration** → **TBM Live**.
2. **Ligne** : choisissez la ligne. Les trams apparaissent en premier ; tapez dans le champ pour filtrer, par exemple « Tram B » ou « Bus 15 ».
3. **Arrêt et sens** : choisissez l'arrêt. Chaque entrée affiche l'arrêt puis la destination, par exemple `Quinconces → Pessac Centre`. Choisissez celle qui correspond au sens dans lequel vous partez.
4. Validez. L'appareil et ses capteurs sont créés.

Pour suivre un autre arrêt, un autre sens ou une autre ligne, recommencez : **Ajouter une entrée** sur la carte TBM Live.

## Entités créées

Pour chaque arrêt suivi, un appareil est créé avec deux capteurs de type horodatage (`timestamp`) :

| Capteur | Valeur |
|---|---|
| **Prochain passage** | Heure du prochain départ |
| **Passage suivant** | Heure du départ d'après |

L'interface affiche nativement « dans 4 minutes ». Le capteur passe en `unknown` s'il n'y a plus de passage prévu dans les deux prochaines heures, par exemple la nuit.

### Attributs

| Attribut | Exemple | Description |
|---|---|---|
| `minutes` | `4` | Minutes avant le passage |
| `realtime` | `true` | `true` si l'horaire vient du suivi en temps réel, `false` s'il est théorique |
| `delay_minutes` | `2` | Écart avec l'horaire théorique |
| `destination` | `Pessac Centre` | Destination affichée sur le véhicule |
| `line` | `Tram B` | Ligne |
| `stop` | `Quinconces` | Arrêt |
| `direction` | `Pessac Centre` | Sens choisi à la configuration |
| `mode` | `tram` | `tram`, `bus` ou `other` |
| `departures` | *liste* | **Prochain passage seulement** : les 6 prochains départs (`time`, `minutes`, `realtime`, `delay_minutes`, `destination`, `cancelled`) |

`minutes` et `departures` ne sont pas enregistrés dans l'historique pour ne pas alourdir la base de données.

## Exemples

### Carte simple

```yaml
type: entities
title: Tram B – Quinconces
entities:
  - entity: sensor.tram_b_quinconces_pessac_centre_prochain_passage
  - entity: sensor.tram_b_quinconces_pessac_centre_passage_suivant
```

Les noms d'entités exacts dépendent de votre configuration : vérifiez-les dans **Paramètres → Entités**.

### Affichage « 4 min · 12 min »

Avec une carte Markdown :

```yaml
type: markdown
content: >
  {% set s = 'sensor.tram_b_quinconces_pessac_centre_prochain_passage' %}
  🚋 **Tram B** :
  {% for d in state_attr(s, 'departures') or [] %}
  {{ d.minutes }} min{{ '' if d.realtime else '*' }}{{ ' · ' if not loop.last }}
  {%- endfor %}
```

L'astérisque signale un horaire théorique.

### Automatisation : prévenir avant de partir

```yaml
alias: Tram dans 5 minutes
triggers:
  - trigger: template
    value_template: >
      {{ state_attr('sensor.tram_b_quinconces_pessac_centre_prochain_passage', 'minutes') == 5 }}
conditions:
  - condition: time
    after: "07:30:00"
    before: "08:30:00"
    weekday: [mon, tue, wed, thu, fri]
actions:
  - action: notify.mobile_app_mon_telephone
    data:
      message: "Le tram B passe dans 5 minutes 🏃"
```

## Bon à savoir

- **Source des données** : flux temps réel SIRI-Lite de Bordeaux Métropole, publié en open data. Les données sont fournies telles quelles. Si le réseau ne transmet pas de position pour un véhicule, l'horaire théorique est affiché et `realtime` vaut `false`.
- **Liste des arrêts** : elle est construite à partir des courses du jour de la ligne, ce qui garantit des arrêts et des sens corrects. Tard le soir, si la ligne ne circule plus, une liste de secours est utilisée : les sens y sont alors moins précis, et il vaut mieux refaire la configuration en journée.
- **Fréquence** : une requête par minute et par arrêt suivi. Merci de ne pas ajouter des dizaines d'arrêts inutiles, l'API est un service public gratuit.

## Dépannage

**« Impossible de joindre l'API temps réel TBM »**
L'API est peut-être momentanément indisponible. Réessayez quelques minutes plus tard et vérifiez que Home Assistant a bien accès à Internet.

**Aucun arrêt proposé pour une ligne**
Réessayez en journée, quand la ligne circule.

**Le capteur reste à `unknown` alors que la ligne circule**
Téléchargez les diagnostics : **Paramètres → Appareils et services → TBM Live → ⋮ → Télécharger les diagnostics**. Ouvrez ensuite une [issue](https://github.com/itsNathanB/ha-tbm-live/issues) en joignant ce fichier. Il contient la dernière réponse de l'API, sans aucune donnée personnelle.

**Activer les journaux détaillés**

```yaml
logger:
  logs:
    custom_components.tbm_live: debug
```

## Feuille de route

- [ ] Carte Lovelace dédiée (prochains passages au style du réseau TBM)
- [ ] Options : intervalle de mise à jour, nombre de passages
- [ ] Messages de perturbation du réseau

## Licence

Code sous licence [MIT](LICENSE).
Données : © Bordeaux Métropole / TBM, [Licence Ouverte / Open Licence](https://www.etalab.gouv.fr/licence-ouverte-open-licence/).
