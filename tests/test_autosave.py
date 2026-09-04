"""Preuves de l'enregistrement automatique.

Il n'y a plus de bouton "Enregistrer": la seule chose qui garantit qu'une saisie
est bien en base, c'est ce que fait le serveur. Ces tests verifient donc les
deux proprietes qui rendent l'automatisme sur: une modification repetee met a
jour la MEME ligne au lieu d'en creer une par frappe, et une modification qui
casserait les comptes est refusee plutot qu'acceptee en silence.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.main import app
from app.models import Account, Forecast, RefundAllocation, Statement, Transaction


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(session):
    """Client HTTP branche sur la base de test, jamais sur la vraie."""
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def compte(session):
    account = Account(name="C/C", bank="Credit Mutuel")
    session.add(account)
    session.commit()
    session.refresh(account)
    session.add(Statement(id=1, account_id=account.id, filename="r.pdf", file_hash="h"))
    session.commit()
    return account


def ajoute_transaction(session, compte, montant, direction, **extra):
    tx = Transaction(
        account_id=compte.id,
        statement_id=1,
        date=date(2026, 7, 15),
        description_raw=extra.pop("description", "operation"),
        amount=montant,
        direction=direction,
        category=extra.pop("category", "Autres"),
        dedup_key=extra.pop("dedup_key", f"k{montant}{direction}"),
        **extra,
    )
    session.add(tx)
    session.commit()
    session.refresh(tx)
    return tx


# --- previsions ------------------------------------------------------------


def payload(**overrides):
    data = {
        "category": "Alimentation",
        "start": "2026-07-01",
        "end": "2026-07-31",
        "amount_planned": "150",
        "forecast_id": "",
    }
    data.update(overrides)
    return data


def test_saisie_repetee_met_a_jour_la_meme_prevision(client, session):
    """Le point critique de l'enregistrement sans bouton: taper "150" ne doit
    pas laisser derriere lui les previsions intermediaires 1, 15 puis 150."""
    premier = client.post("/forecasts/save", data=payload(amount_planned="1"))
    assert premier.status_code == 200
    identifiant = premier.json()["id"]

    for montant in ("15", "150"):
        suivant = client.post(
            "/forecasts/save",
            data=payload(amount_planned=montant, forecast_id=str(identifiant)),
        )
        assert suivant.json()["id"] == identifiant

    previsions = session.exec(select(Forecast)).all()
    assert len(previsions) == 1
    assert previsions[0].amount_planned == 150.0


def test_chevauchement_refuse_sans_rien_ecrire(client, session):
    client.post("/forecasts/save", data=payload())

    refus = client.post(
        "/forecasts/save",
        data=payload(start="2026-07-15", end="2026-08-15", amount_planned="90"),
    )

    assert refus.status_code == 422
    assert "chevauche" in refus.json()["error"]
    assert len(session.exec(select(Forecast)).all()) == 1, "aucune prevision en trop"


def test_une_prevision_ne_se_chevauche_pas_elle_meme(client, session):
    cree = client.post("/forecasts/save", data=payload()).json()

    deplacee = client.post(
        "/forecasts/save",
        data=payload(start="2026-07-10", end="2026-08-10", forecast_id=str(cree["id"])),
    )

    assert deplacee.status_code == 200
    prevision = session.get(Forecast, cree["id"])
    assert prevision.period_end == date(2026, 8, 10)


@pytest.mark.parametrize(
    "donnees, extrait",
    [
        (payload(amount_planned="0"), "supérieur à 0"),
        (payload(start="2026-07-31", end="2026-07-01"), "date de fin"),
        (payload(start="", end=""), "calendrier"),
    ],
)
def test_saisies_invalides_refusees(client, session, donnees, extrait):
    refus = client.post("/forecasts/save", data=donnees)

    assert refus.status_code == 422
    assert extrait in refus.json()["error"]
    assert session.exec(select(Forecast)).all() == []


# --- transactions ----------------------------------------------------------


def test_changement_de_categorie_enregistre(client, session, compte):
    tx = ajoute_transaction(session, compte, 30.0, "debit")

    reponse = client.post(
        f"/transactions/{tx.id}/update",
        data={"category": "Alimentation"},
        headers={"X-Requested-With": "fetch"},
    )

    assert reponse.status_code == 200 and reponse.json()["ok"] is True
    session.refresh(tx)
    assert tx.category == "Alimentation"
    assert tx.is_manual_override is True


def test_virement_interne_refuse_si_repartition_en_cours(client, session, compte):
    """Un virement interne est exclu des totaux. Si la transaction participait a
    une repartition, le montant disparaitrait des revenus sans disparaitre des
    depenses — l'egalite (revenus - depenses) serait rompue."""
    depense = ajoute_transaction(session, compte, 100.0, "debit", dedup_key="d1")
    remboursement = ajoute_transaction(session, compte, 40.0, "credit", dedup_key="c1")
    session.add(
        RefundAllocation(
            refund_transaction_id=remboursement.id,
            expense_transaction_id=depense.id,
            amount=40.0,
        )
    )
    session.commit()

    refus = client.post(
        f"/transactions/{remboursement.id}/update",
        data={"category": "Autres", "is_internal_transfer": "true"},
        headers={"X-Requested-With": "fetch"},
    )

    assert refus.status_code == 422
    assert "répartition" in refus.json()["error"]
    session.refresh(remboursement)
    assert remboursement.is_internal_transfer is False, "l'etat en base n'a pas bouge"


def test_virement_interne_accepte_sans_repartition(client, session, compte):
    tx = ajoute_transaction(session, compte, 60.0, "credit")

    reponse = client.post(
        f"/transactions/{tx.id}/update",
        data={"category": "Epargne/Virement interne", "is_internal_transfer": "true"},
        headers={"X-Requested-With": "fetch"},
    )

    assert reponse.json()["ok"] is True
    session.refresh(tx)
    assert tx.is_internal_transfer is True


def test_decocher_virement_interne_reste_possible(client, session, compte):
    """Le garde-fou ne doit bloquer que la mise en interne, jamais la sortie:
    sinon une transaction mal marquee serait definitivement coincee."""
    tx = ajoute_transaction(session, compte, 60.0, "credit", is_internal_transfer=True)

    reponse = client.post(
        f"/transactions/{tx.id}/update",
        data={"category": "Autres"},  # case decochee: le champ n'est pas envoye
        headers={"X-Requested-With": "fetch"},
    )

    assert reponse.json()["ok"] is True
    session.refresh(tx)
    assert tx.is_internal_transfer is False


def test_transaction_inconnue_renvoie_une_erreur_lisible(client, session):
    refus = client.post(
        "/transactions/9999/update",
        data={"category": "Autres"},
        headers={"X-Requested-With": "fetch"},
    )

    assert refus.status_code == 422
    assert "introuvable" in refus.json()["error"]
