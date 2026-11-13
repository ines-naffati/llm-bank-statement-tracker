"""Preuves du garde-fou anti-fuite.

Un controle de securite qui laisse passer, ou qui crie a tort, finit ignore.
Les deux comptent donc autant l'un que l'autre.

Regle de ce fichier: aucune donnee reelle, meme "pour tester". Les chaines qui
doivent ressembler a un secret sont assemblees a l'execution plutot qu'ecrites
telles quelles - sinon ce fichier declencherait le controle qu'il verifie, et on
prendrait l'habitude de passer outre.
"""

import sqlite3

import pytest

from scripts import check_no_personal_data as garde

# Assemblees morceau par morceau: le fichier ne contient aucun motif complet,
# mais le test travaille bien sur la chaine entiere.
FAUSSE_CLE = "sk-" + "ant-api03-" + "AbCdEf0123456789XyZwVuTsRq"
FAUX_IBAN = "FR" + "76" + "30004" + "00003" + "12345" + "67890" + "143"
FAUX_NUMERO = "9" * 14


@pytest.mark.parametrize(
    "texte, attendu",
    [
        (f"ANTHROPIC_API_KEY={FAUSSE_CLE}", "clé API Anthropic"),
        (f"IBAN {FAUX_IBAN}", "IBAN"),
        (f"compte {FAUX_NUMERO} au 31/07", "numéro à 11 chiffres ou plus"),
    ],
)
def test_formes_sensibles_reconnues(texte, attendu):
    trouves = [label for motif, label in garde.SHAPES if motif.search(texte)]
    assert attendu in trouves


@pytest.mark.parametrize(
    "texte",
    [
        "ANTHROPIC_API_KEY=sk-ant-...",             # le placeholder de .env.example
        "MAX_TOKENS = 16000",                        # une constante du code
        "date(2026, 9, 4)",                          # une date
        "hashlib.md5(raw.encode()).hexdigest()",     # du code ordinaire
    ],
)
def test_pas_de_fausse_alerte_sur_du_code_ordinaire(texte):
    assert not [label for motif, label in garde.SHAPES if motif.search(texte)]


def _base_avec(tmp_path, monkeypatch, nom, reference, fichier):
    chemin = tmp_path / "suivi.db"
    connection = sqlite3.connect(chemin)
    connection.execute("CREATE TABLE account (name TEXT, external_ref TEXT)")
    connection.execute("CREATE TABLE statement (filename TEXT, stored_filename TEXT)")
    connection.execute("INSERT INTO account VALUES (?, ?)", (nom, reference))
    connection.execute("INSERT INTO statement VALUES (?, ?)", (fichier, fichier))
    connection.commit()
    connection.close()
    monkeypatch.setattr(garde, "DB_PATH", chemin)
    return garde.needles_from_database()


def test_les_identifiants_reels_sont_extraits(tmp_path, monkeypatch):
    needles = _base_avec(
        tmp_path, monkeypatch,
        nom="C/C EUROCOMPTE JEUNE MME CAMILLE DUPONTEL",
        reference=FAUX_NUMERO,
        fichier=f"Extrait de comptes DUPONTEL au 2026-07-31.pdf",
    )

    assert "dupontel" in needles, "un nom de famille doit etre surveille"
    assert FAUX_NUMERO in needles, "un numero de compte doit etre surveille"


def test_le_vocabulaire_bancaire_generique_est_ignore(tmp_path, monkeypatch):
    needles = _base_avec(
        tmp_path, monkeypatch,
        nom="Extrait de compte en EUR",
        reference=None,
        fichier="Releve-compte-30-06-2026.pdf",
    )

    for mot in ("compte", "extrait", "releve"):
        assert mot not in needles, f"'{mot}' declencherait le controle sur la moitie du code"


def test_les_jetons_chiffres_echappent_a_la_liste_generique(tmp_path, monkeypatch):
    """Meme un mot generique devient suspect des qu'il porte des chiffres: le
    prefixe d'un fichier stocke identifie un document precis, pas une notion."""
    prefixe = "b" * 8 + "1234" + "c" * 8
    needles = _base_avec(
        tmp_path, monkeypatch, nom="Compte", reference=None,
        fichier=f"{prefixe}_releve.pdf",
    )

    assert prefixe in needles


def test_base_absente_ne_fait_pas_echouer(tmp_path, monkeypatch):
    monkeypatch.setattr(garde, "DB_PATH", tmp_path / "inexistante.db")
    assert garde.needles_from_database() == set()


def test_image_binaire_signalee_sans_fausse_alerte(tmp_path, monkeypatch):
    """Une capture d'ecran ne doit ni faire planter le controle, ni declencher
    une fausse alerte sur ses octets: elle est listee pour verification visuelle."""
    import subprocess

    repo = tmp_path / "depot"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "capture.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"2" * 30 + b"\x00" * 10)
    (repo / "notes.txt").write_text("rien de sensible\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(garde, "needles_from_database", lambda: set())

    assert garde.is_binary("capture.jpg", "index")
    assert not garde.is_binary("notes.txt", "index")
    assert garde.main() == 0
