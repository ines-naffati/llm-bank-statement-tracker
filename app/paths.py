"""Emplacements sur disque, isoles ici pour que le module de sauvegarde et le
module de base de donnees puissent tous deux s'y referer sans s'importer l'un
l'autre."""

import os
from pathlib import Path

# SUIVI_DATA_DIR permet de lancer l'application sur une autre base (ex: la base de
# demonstration creee par scripts/seed_demo.py) sans jamais toucher aux vraies donnees.
DATA_DIR = Path(
    os.environ.get("SUIVI_DATA_DIR") or Path(__file__).resolve().parent.parent / "data"
).resolve()
UPLOADS_DIR = DATA_DIR / "uploads"
BACKUPS_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "suivi.db"

for _directory in (DATA_DIR, UPLOADS_DIR, BACKUPS_DIR):
    _directory.mkdir(parents=True, exist_ok=True)
