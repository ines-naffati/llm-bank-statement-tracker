"""Restaure la base a partir d'une sauvegarde.

Une sauvegarde qu'on ne sait pas restaurer sous stress ne sert a rien. Ce script
est donc volontairement bavard: il montre ce que contient chaque sauvegarde
AVANT de demander confirmation, et il ne detruit jamais rien - la base actuelle
est mise de cote, pas supprimee, pour qu'une restauration malencontreuse reste
elle-meme reversible.

Usage:  python -m scripts.restore_backup

Arrete l'application (Ctrl+C dans le terminal uvicorn) avant de lancer ce
script: remplacer une base pendant qu'elle est ouverte laisserait le journal
WAL desynchronise du fichier principal.
"""

import shutil
import sqlite3
import sys
from datetime import datetime

from app.paths import BACKUPS_DIR, DB_PATH
from app.services import backup

TABLES = ["account", "statement", "transaction", "refundallocation", "forecast"]


def summarize(path):
    """Nombre de lignes par table, pour reconnaitre la bonne sauvegarde d'un
    coup d'oeil plutot que sur la foi de son horodatage."""
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return "illisible"
    counts = []
    try:
        for table in TABLES:
            try:
                total = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            except sqlite3.Error:
                continue
            if total:
                counts.append(f"{total} {table}")
    finally:
        connection.close()
    return ", ".join(counts) or "base vide"


def main() -> int:
    backups = backup.list_backups()
    if not backups:
        print(f"Aucune sauvegarde dans {BACKUPS_DIR}.")
        return 1

    print(f"Base actuelle : {DB_PATH}")
    print(f"                {summarize(DB_PATH) if DB_PATH.exists() else 'absente'}\n")
    print("Sauvegardes disponibles, de la plus recente a la plus ancienne :\n")
    for index, path in enumerate(backups, start=1):
        info = backup.describe(path)
        print(f"  [{index:>2}] {info['taken_at']:%d/%m/%Y %H:%M:%S}  ({info['reason']})")
        print(f"       {summarize(path)} — {info['size_kb']} ko")

    choice = input("\nNumero a restaurer (Entree pour annuler) : ").strip()
    if not choice:
        print("Annule, rien n'a ete touche.")
        return 0
    try:
        chosen = backups[int(choice) - 1]
    except (ValueError, IndexError):
        print("Numero invalide, rien n'a ete touche.")
        return 1

    print(f"\nCela remplacera la base actuelle par : {chosen.name}")
    print(f"  actuelle  : {summarize(DB_PATH) if DB_PATH.exists() else 'absente'}")
    print(f"  restauree : {summarize(chosen)}")
    if input('Tape "restaurer" pour confirmer : ').strip().lower() != "restaurer":
        print("Annule, rien n'a ete touche.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if DB_PATH.exists():
        # La base actuelle est mise de cote, jamais supprimee: si la sauvegarde
        # choisie n'etait pas la bonne, on peut encore revenir en arriere.
        mise_de_cote = BACKUPS_DIR / f"suivi-{stamp}-avant-restauration.db"
        shutil.copy2(DB_PATH, mise_de_cote)
        print(f"\nBase actuelle mise de cote : {mise_de_cote.name}")
        DB_PATH.unlink()

    # Les fichiers annexes du mode WAL appartiennent a l'ancienne base: les
    # laisser en place a cote d'un nouveau fichier principal corromprait tout.
    for suffix in ("-wal", "-shm"):
        annexe = DB_PATH.with_name(DB_PATH.name + suffix)
        if annexe.exists():
            annexe.rename(BACKUPS_DIR / f"suivi-{stamp}-avant-restauration.db{suffix}")

    shutil.copy2(chosen, DB_PATH)
    print(f"Restaure depuis {chosen.name}")
    print(f"Contenu : {summarize(DB_PATH)}")
    print("\nRelance l'application : uvicorn app.main:app --reload")
    return 0


if __name__ == "__main__":
    sys.exit(main())
