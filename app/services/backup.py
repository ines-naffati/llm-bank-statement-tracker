"""Sauvegardes automatiques de la base.

Toute l'application tient dans un seul fichier SQLite. Une fausse manoeuvre, un
disque qui lache, un import qui part de travers, et l'historique disparait. Ce
module garantit qu'il existe toujours un etat anterieur vers lequel revenir.

Trois declencheurs, parce qu'ils protegent de trois accidents differents:

- au demarrage de l'application, pour figer l'etat "avant la session du jour";
- avant toute operation qui ecrit en masse (import d'un releve), pour pouvoir
  revenir a l'instant d'avant si l'extraction est mauvaise;
- apres chaque ecriture (categorie, prevision, affectation), mais au plus une
  fois toutes les quelques minutes: sans ce garde-fou on creerait un fichier par
  frappe, avec on ne perd jamais plus de quelques minutes de saisie.

On utilise l'API de sauvegarde en ligne de SQLite (`Connection.backup`) plutot
qu'une copie de fichier: elle produit une base coherente meme si l'application
ecrit pendant ce temps, la ou un `cp` peut capturer un fichier a moitie ecrit -
d'autant plus en mode WAL, ou une partie des donnees vit dans un fichier annexe
qu'une simple copie laisserait derriere elle.

Regle de conduite: une sauvegarde qui echoue ne doit JAMAIS faire echouer
l'enregistrement de l'utilisatrice. Tout est donc capture et journalise.
"""

import logging
import re
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from app.paths import BACKUPS_DIR, DB_PATH

logger = logging.getLogger(__name__)

# Intervalle minimum entre deux sauvegardes declenchees par une petite ecriture.
# Cinq minutes: assez court pour qu'une perte reste indolore, assez long pour
# qu'une seance de recategorisation ne laisse pas cinquante fichiers.
MIN_INTERVAL_SECONDS = 300

# Retention. On garde les sauvegardes recentes en detail, puis une par jour:
# ce qu'on cherche a rattraper est soit une betise d'il y a dix minutes, soit
# "l'etat d'avant-hier", jamais la 300e sauvegarde d'il y a trois semaines.
KEEP_RECENT = 20
KEEP_DAILY_DAYS = 60

_STAMP = "%Y%m%d-%H%M%S"
_NAME_RE = re.compile(r"^suivi-(\d{8}-\d{6})-(.+)\.db$")

_lock = threading.Lock()
_last_snapshot_at: Optional[datetime] = None


def _slug(reason: str) -> str:
    """Reduit un motif libre a ce qui peut tenir dans un nom de fichier."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-")
    return cleaned[:40] or "auto"


def timestamp_of(path: Path) -> datetime:
    """Horodatage porte par le nom du fichier, avec la date de modification en
    secours si le nom n'a pas la forme attendue (fichier renomme a la main)."""
    match = _NAME_RE.match(path.name)
    if match:
        try:
            return datetime.strptime(match.group(1), _STAMP)
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def list_backups() -> List[Path]:
    """Sauvegardes existantes, de la plus recente a la plus ancienne."""
    if not BACKUPS_DIR.exists():
        return []
    files = [p for p in BACKUPS_DIR.glob("suivi-*.db") if p.is_file()]
    return sorted(files, key=timestamp_of, reverse=True)


def snapshot(reason: str = "auto", db_path: Path = DB_PATH) -> Optional[Path]:
    """Prend une sauvegarde immediate. Renvoie le fichier cree, ou None si la
    base n'existe pas encore ou si la sauvegarde a echoue."""
    if not Path(db_path).exists():
        return None

    destination = BACKUPS_DIR / f"suivi-{datetime.now().strftime(_STAMP)}-{_slug(reason)}.db"
    source = target = None
    try:
        with _lock:
            source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
            target = sqlite3.connect(destination)
            source.backup(target)
            target.close()
            source.close()
            source = target = None

            global _last_snapshot_at
            _last_snapshot_at = datetime.now()
    except Exception:  # une sauvegarde ratee ne doit pas casser l'enregistrement
        logger.exception("Sauvegarde impossible (%s)", reason)
        for connection in (target, source):
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
        destination.unlink(missing_ok=True)  # ne pas laisser un fichier tronque
        return None

    prune()
    logger.info("Sauvegarde: %s", destination.name)
    return destination


def last_snapshot_at() -> Optional[datetime]:
    """Date de la derniere sauvegarde, y compris celles prises par un processus
    precedent.

    Le compteur en memoire disparait a chaque redemarrage; en developpement,
    `uvicorn --reload` redemarre a chaque fichier enregistre, ce qui produisait
    une sauvegarde par frappe. On retombe donc sur ce qui est reellement sur le
    disque quand la memoire ne sait rien."""
    with _lock:
        if _last_snapshot_at is not None:
            return _last_snapshot_at
    existing = list_backups()
    return timestamp_of(existing[0]) if existing else None


def note_change(reason: str = "auto", db_path: Path = DB_PATH) -> Optional[Path]:
    """Signale une ecriture en base. Ne sauvegarde que si la derniere sauvegarde
    remonte a plus de MIN_INTERVAL_SECONDS - c'est ce qui permet de brancher cet
    appel sur *toutes* les ecritures sans saturer le disque."""
    last = last_snapshot_at()
    if last is not None and (datetime.now() - last).total_seconds() < MIN_INTERVAL_SECONDS:
        return None
    return snapshot(reason, db_path=db_path)


def prune(now: Optional[datetime] = None) -> List[Path]:
    """Applique la retention et renvoie les fichiers supprimes.

    On conserve les KEEP_RECENT plus recentes, puis la derniere sauvegarde de
    chacun des KEEP_DAILY_DAYS derniers jours ou il y en a eu au moins une."""
    files = list_backups()
    keep = set(files[:KEEP_RECENT])

    newest_of_day = {}
    for path in files:  # deja tries du plus recent au plus ancien
        day = timestamp_of(path).date()
        newest_of_day.setdefault(day, path)
    for day in sorted(newest_of_day, reverse=True)[:KEEP_DAILY_DAYS]:
        keep.add(newest_of_day[day])

    removed = []
    for path in files:
        if path in keep:
            continue
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            logger.warning("Sauvegarde non supprimee: %s", path.name)
    return removed


def describe(path: Path) -> dict:
    """Resume lisible d'une sauvegarde, pour un affichage ou un script."""
    match = _NAME_RE.match(path.name)
    stat = path.stat()
    return {
        "path": path,
        "name": path.name,
        "taken_at": timestamp_of(path),
        "reason": match.group(2) if match else "inconnu",
        "size_kb": round(stat.st_size / 1024, 1),
    }
