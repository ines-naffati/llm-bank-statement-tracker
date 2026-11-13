# Suivi de dépenses

Application locale pour importer des relevés bancaires PDF (Crédit Mutuel, Boursorama...),
en extraire les transactions via une IA (API Claude ou Ollama en local), et comparer dépenses
réelles et prévisions.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Puis dans `.env`, choisir le moteur d'extraction avec `EXTRACTION_BACKEND` :

- **`claude`** (par défaut) — nécessite une clé `ANTHROPIC_API_KEY` (console.anthropic.com), payant
  à l'usage (~1€/an pour 2 comptes importés mensuellement), le plus fiable.
- **`ollama`** — gratuit et 100% local, nécessite [Ollama](https://ollama.com) installé et lancé
  (`ollama serve`), avec un modèle téléchargé (`ollama pull llama3.2:3b`, déjà présent sur cette
  machine, ou `ollama pull qwen2.5:7b` pour une extraction plus fiable si besoin — ~4,7 Go). Moins
  précis que Claude, notamment sur la catégorisation et la détection des virements internes — la
  vérification automatique du solde à l'import sert de garde-fou pour repérer les erreurs
  d'extraction.

## Lancement

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

Ouvrir http://127.0.0.1:8000

## Utilisation

1. Aller sur **Relevés** et importer un PDF. Les comptes/sous-comptes présents dans le document
   (compte courant, livrets...) sont détectés et créés automatiquement.
2. Vérifier le statut de rapprochement (solde recalculé vs solde affiché) — un écart signale un
   relevé à contrôler manuellement.
3. Ajuster si besoin la catégorie ou le statut "virement interne" d'une transaction dans
   **Transactions** — la catégorie est mémorisée pour ce commerçant.
4. Saisir des prévisions par catégorie et par période dans **Prévisions**.
5. Consulter **Suivi de dépenses** (page d'accueil) pour la comparaison prévu/réel.

Il n'y a **aucun bouton « Enregistrer »** : chaque modification part au serveur dès qu'elle est
faite, et un bandeau confirme l'enregistrement (ou explique le refus). Une saisie refusée est
annulée à l'écran, pour que l'affichage ne montre jamais une valeur absente de la base.

## Sauvegardes

Les sauvegardes sont automatiques, dans `data/backups/` :

- au démarrage de l'application ;
- avant chaque import de relevé (la seule opération qui écrit en masse à partir d'un texte
  produit par un modèle, donc faillible) ;
- après les modifications, au plus une fois toutes les 5 minutes.

Elles utilisent l'API de sauvegarde en ligne de SQLite : le fichier produit est toujours une base
cohérente, même si l'application écrit pendant ce temps. La rétention garde les 20 plus récentes
puis une par jour sur 60 jours.

Pour restaurer (application arrêtée) :

```bash
python -m scripts.restore_backup
```

Le script affiche le contenu de chaque sauvegarde avant de demander confirmation, et met la base
actuelle de côté au lieu de l'écraser.

Ces sauvegardes vivent sur le même disque que la base : elles protègent d'une fausse manœuvre ou
d'un import raté, **pas** d'une panne de disque. Une copie de `data/` ailleurs (Time Machine, un
disque externe) reste nécessaire.

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

## Confidentialité

Avec `EXTRACTION_BACKEND=claude`, le texte extrait des relevés est envoyé à l'API Claude
(Anthropic) pour être structuré en transactions — c'est la seule donnée qui quitte la machine.
Avec `EXTRACTION_BACKEND=ollama`, rien ne quitte la machine. Dans les deux cas, la base SQLite et
les PDF importés sont stockés dans `data/` (exclu de git).
