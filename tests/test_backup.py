"""Preuves du systeme de sauvegarde.

Une sauvegarde qu'on n'a jamais restauree n'est pas une sauvegarde. Ces tests
verifient donc ce qui compte vraiment: que le fichier produit est une base
lisible contenant les memes lignes, y compris pendant que l'application ecrit.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Account, Transaction
from app.services import backup


@pytest.fixture
def backups_dir(tmp_path, monkeypatch):
    """Redirige les sauvegardes vers un dossier temporaire et repart d'un
    compteur vierge, pour ne jamais toucher aux vraies sauvegardes."""
    directory = tmp_path / "backups"
    directory.mkdir()
    monkeypatch.setattr(backup, "BACKUPS_DIR", directory)
    monkeypatch.setattr(backup, "_last_snapshot_at", None)
    return directory


@pytest.fixture
def db(tmp_path):
    """Une vraie base sur disque (pas en memoire): la sauvegarde en ligne de
    SQLite ne peut se tester que sur un fichier."""
    path = tmp_path / "suivi.db"
    engine = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Account(name="C/C", bank="Credit Mutuel"))
        session.commit()
    return path


def rows(db_path, table):
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    finally:
        connection.close()


def test_snapshot_produces_a_readable_copy(db, backups_dir):
    created = backup.snapshot("test", db_path=db)

    assert created is not None and created.exists()
    assert created.parent == backups_dir
    assert rows(created, "account") == rows(db, "account") == 1
    # La copie doit etre une base saine, pas seulement un fichier non vide.
    connection = sqlite3.connect(created)
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()


def test_snapshot_is_independent_from_later_changes(db, backups_dir):
    """Le point crucial: une sauvegarde doit figer l'etat du moment. Si elle
    refletait les ecritures suivantes, elle ne servirait a rien."""
    created = backup.snapshot("avant", db_path=db)

    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        session.add(Account(name="Livret", bank="Credit Mutuel"))
        session.commit()

    assert rows(db, "account") == 2
    assert rows(created, "account") == 1  # la sauvegarde n'a pas bouge


def test_snapshot_while_a_write_is_in_flight(db, backups_dir):
    """Une transaction ouverte et non validee ne doit pas se retrouver dans la
    sauvegarde - c'est precisement ce qu'une copie de fichier ne garantit pas."""
    engine = create_engine(f"sqlite:///{db}")
    session = Session(engine)
    session.add(Account(name="En cours", bank="X"))
    session.flush()  # ecrit dans la transaction, sans valider

    created = backup.snapshot("pendant-ecriture", db_path=db)

    session.rollback()
    session.close()
    assert rows(created, "account") == 1


def test_missing_database_is_not_an_error(tmp_path, backups_dir):
    assert backup.snapshot("test", db_path=tmp_path / "inexistante.db") is None


def test_note_change_spaces_out_snapshots(db, backups_dir, monkeypatch):
    first = backup.note_change("ecriture-1", db_path=db)
    second = backup.note_change("ecriture-2", db_path=db)

    assert first is not None
    assert second is None, "deux ecritures rapprochees ne doivent produire qu'un fichier"

    # Une fois l'intervalle ecoule, la sauvegarde suivante repart.
    monkeypatch.setattr(
        backup,
        "_last_snapshot_at",
        datetime.now() - timedelta(seconds=backup.MIN_INTERVAL_SECONDS + 1),
    )
    assert backup.note_change("ecriture-3", db_path=db) is not None


def test_debounce_survives_a_restart(db, backups_dir, monkeypatch):
    """Le compteur en memoire disparait a chaque redemarrage. Sans repli sur le
    disque, `uvicorn --reload` sauvegardait a chaque fichier enregistre."""
    backup.note_change("avant-redemarrage", db_path=db)
    monkeypatch.setattr(backup, "_last_snapshot_at", None)  # simule un redemarrage

    assert backup.note_change("apres-redemarrage", db_path=db) is None
    assert len(backup.list_backups()) == 1


def _fake_backup(directory, taken_at, reason="auto"):
    path = directory / f"suivi-{taken_at.strftime('%Y%m%d-%H%M%S')}-{reason}.db"
    path.write_bytes(b"")
    return path


def test_prune_keeps_recent_and_one_per_day(backups_dir, monkeypatch):
    monkeypatch.setattr(backup, "KEEP_RECENT", 3)
    monkeypatch.setattr(backup, "KEEP_DAILY_DAYS", 2)
    now = datetime(2026, 9, 4, 18, 0, 0)

    today = [_fake_backup(backups_dir, now - timedelta(minutes=m)) for m in (0, 5, 10, 15, 20)]
    hier = [_fake_backup(backups_dir, now - timedelta(days=1, minutes=m)) for m in (0, 30)]
    vieux = [_fake_backup(backups_dir, now - timedelta(days=9, minutes=m)) for m in (0, 30)]

    backup.prune()
    restants = set(backup.list_backups())

    assert set(today[:3]) <= restants, "les 3 plus recentes sont conservees"
    assert hier[0] in restants, "la derniere sauvegarde d'hier est conservee"
    assert hier[1] not in restants, "les doublons du meme jour partent"
    assert not (set(vieux) & restants), "au-dela de la retention journaliere, tout part"


def test_prune_never_empties_the_directory(backups_dir):
    _fake_backup(backups_dir, datetime(2020, 1, 1))
    backup.prune()
    assert len(backup.list_backups()) == 1, "la seule sauvegarde existante ne doit jamais partir"


def test_describe_reads_back_the_reason(db, backups_dir):
    created = backup.snapshot("avant-import", db_path=db)
    info = backup.describe(created)

    assert info["reason"] == "avant-import"
    assert info["size_kb"] > 0
    assert (datetime.now() - info["taken_at"]).total_seconds() < 60


def test_transactions_survive_the_round_trip(db, backups_dir):
    """Verification de bout en bout: on sauvegarde, on detruit, on restaure."""
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        account = session.exec(select(Account)).one()
        for i in range(50):
            session.add(
                Transaction(
                    account_id=account.id,
                    statement_id=1,
                    date=datetime(2026, 7, 1).date(),
                    description_raw=f"achat {i}",
                    amount=10.0 + i,
                    direction="debit",
                    dedup_key=f"k{i}",
                )
            )
        session.commit()

    created = backup.snapshot("avant-catastrophe", db_path=db)
    db.unlink()  # la base disparait
    assert not db.exists()

    created.replace(db)  # restauration
    assert rows(db, "transaction") == 50
    with Session(create_engine(f"sqlite:///{db}")) as session:
        montants = [t.amount for t in session.exec(select(Transaction)).all()]
    assert sum(montants) == sum(10.0 + i for i in range(50))
