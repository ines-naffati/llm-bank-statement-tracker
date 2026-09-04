"""Garde-fou commun a tous les tests.

Une suite de tests ne doit jamais ecrire dans les vraies donnees. Le demarrage
de l'application prend un instantane de la base: sans cette isolation, lancer
les tests deposerait des fichiers dans les sauvegardes reelles.
"""

import pytest

from app.services import backup


@pytest.fixture(autouse=True)
def sauvegardes_isolees(tmp_path, monkeypatch):
    directory = tmp_path / "backups-test"
    directory.mkdir()
    monkeypatch.setattr(backup, "BACKUPS_DIR", directory)
    monkeypatch.setattr(backup, "_last_snapshot_at", None)
    return directory
