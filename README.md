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
4. Saisir des prévisions par catégorie/mois dans **Prévisions**.
5. Consulter le **Tableau de bord** pour la comparaison prévu/réel du mois.

## Confidentialité

Avec `EXTRACTION_BACKEND=claude`, le texte extrait des relevés est envoyé à l'API Claude
(Anthropic) pour être structuré en transactions — c'est la seule donnée qui quitte la machine.
Avec `EXTRACTION_BACKEND=ollama`, rien ne quitte la machine. Dans les deux cas, la base SQLite et
les PDF importés sont stockés dans `data/` (exclu de git).
