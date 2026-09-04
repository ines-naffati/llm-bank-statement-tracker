"""Emplacements sur disque, isoles ici pour que le module de sauvegarde et le
module de base de donnees puissent tous deux s'y referer sans s'importer l'un
l'autre."""

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
BACKUPS_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "suivi.db"

for _directory in (DATA_DIR, UPLOADS_DIR, BACKUPS_DIR):
    _directory.mkdir(exist_ok=True)
