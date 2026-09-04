from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from app.paths import BACKUPS_DIR, DATA_DIR, DB_PATH, UPLOADS_DIR  # noqa: F401  (re-exportes)
from app.services.backup import note_change

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record):
    """Reglages appliques a chaque connexion.

    - `foreign_keys=ON`: SQLite ignore les cles etrangeres par defaut. Sans ce
      pragma, rien n'empeche une affectation de remboursement de pointer vers
      une transaction supprimee.
    - `journal_mode=WAL`: survit bien mieux a une interruption qu'un journal
      classique, et laisse la sauvegarde lire pendant que l'application ecrit.
    - `synchronous=FULL`: chaque validation est reellement sur le disque avant
      de repondre. Plus lent en theorie, imperceptible sur une base de cette
      taille, et c'est exactement ce qu'on veut pour des donnees bancaires.
    - `busy_timeout`: plutot que d'echouer immediatement si la base est occupee,
      on patiente cinq secondes.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=FULL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


@event.listens_for(Session, "after_commit")
def _backup_after_commit(session):
    """Sauvegarde apres chaque ecriture validee, quelle qu'en soit l'origine.

    Branche au niveau de la session plutot que dans chaque route: une
    fonctionnalite ajoutee plus tard sera protegee sans que personne n'ait a y
    penser. `note_change` espace lui-meme les sauvegardes, cet appel est donc
    tres bon marche la plupart du temps."""
    try:
        if session.get_bind() is not engine:
            return  # session de test sur une autre base: rien a sauvegarder
    except Exception:
        return
    note_change("apres-ecriture")


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
