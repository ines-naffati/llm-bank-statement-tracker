"""Verifie qu'aucune donnee personnelle ne part dans un commit.

Le piege evident serait d'ecrire ici la liste des choses a ne pas publier: le
nom de la titulaire, son numero de compte... Ce fichier etant lui-meme versionne,
la liste serait exactement la fuite qu'elle pretend empecher.

On procede donc a l'envers: les identifiants reels vivent dans `data/`, qui n'est
jamais versionne. On les lit en local, et on verifie qu'aucun n'apparait dans ce
qui est sur le point de partir. Rien de sensible n'est ecrit dans ce script, et
il reste juste meme si les donnees changent.

Usage:  python -m scripts.check_no_personal_data
Sortie: 0 si tout est propre, 1 si quelque chose doit etre retire de l'index.
"""

import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from app.paths import DB_PATH

# Motifs generiques, valables meme sans base locale.
SHAPES = [
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "clé API Anthropic"),
    (re.compile(r"\bFR\d{2}[ ]?[0-9A-Z]{4}([ ]?[0-9A-Z]{4}){2,}"), "IBAN"),
    (re.compile(r"(?<!\d)\d{11,}(?!\d)"), "numéro à 11 chiffres ou plus"),
]

# Un identifiant plus court produirait trop de fausses alertes.
MIN_TOKEN = 5

# Les libelles de comptes et noms de fichiers de releves sont faits en majorite
# de vocabulaire bancaire generique ("Extrait de compte en EUR", "C/C EUROCOMPTE
# JEUNE"). Sans cette liste, le controle se declencherait sur le mot "compte"
# present dans la moitie du code, et on prendrait l'habitude de l'ignorer - un
# controle qu'on ignore ne protege plus de rien.
GENERIC = {
    "account", "accounts", "banque", "bleu", "bourso", "boursobank", "compte",
    "comptes", "courant", "credit", "depense", "depenses", "epargne", "euro",
    "eurocompte", "extrait", "extraits", "fichier", "jeune", "livret", "mutuel",
    "operation", "operations", "populaire", "releve", "releves", "statement",
    "statements", "suivi", "transaction", "transactions", "upload", "uploads",
    "virement", "virements",
}


def files_to_check():
    """Ce qui partirait reellement, et sa provenance.

    Piege evite ici: se limiter aux fichiers indexes. Quand tout est deja
    committe, l'index est vide et le controle ne verifie plus rien tout en
    affichant un resultat rassurant. Or un push envoie des commits, pas un
    index. On retombe donc sur l'integralite des fichiers suivis."""
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True, text=True, check=True,
    )
    staged = [line for line in out.stdout.splitlines() if line.strip()]
    if staged:
        return staged, "index"

    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    )
    return [line for line in out.stdout.splitlines() if line.strip()], "suivis"


def content_of(path, source):
    """Contenu tel que git le detient - dans l'index ou dans le dernier commit -
    et non tel qu'il est sur le disque: c'est bien ce qui partirait.

    `errors="replace"`: un fichier binaire (capture d'ecran, image) ne doit pas
    faire planter le controle - un controle qui plante laisse tout passer. Le
    texte lisible qu'il contient reste verifie."""
    revision = f":{path}" if source == "index" else f"HEAD:{path}"
    out = subprocess.run(["git", "show", revision], capture_output=True)
    return out.stdout.decode("utf-8", errors="replace") if out.returncode == 0 else ""


def is_binary(path, source):
    """Une image ou un PDF ne se lit pas comme du texte: y chercher des motifs
    produit de fausses alertes (des octets d'image qui ressemblent a un numero),
    et un controle qui crie a tort finit ignore. On les signale plutot pour une
    verification visuelle."""
    revision = f":{path}" if source == "index" else f"HEAD:{path}"
    out = subprocess.run(["git", "show", revision], capture_output=True)
    data = out.stdout[:8000]
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        # Une coupure au milieu d'un caractere multi-octets en fin d'extrait n'est pas du binaire.
        return exc.start < len(data) - 4
    return False


def needles_from_database():
    """Identifiants reels tires de la base locale: libelles de comptes, numeros,
    noms de fichiers de releves, et les mots distinctifs qu'ils contiennent."""
    if not Path(DB_PATH).exists():
        return set()

    values = set()
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        queries = [
            'SELECT name, external_ref FROM "account"',
            'SELECT filename, stored_filename FROM "statement"',
        ]
        for query in queries:
            try:
                for row in connection.execute(query):
                    values.update(cell for cell in row if cell)
            except sqlite3.Error:
                continue
    finally:
        connection.close()

    needles = set()
    for value in values:
        for token in re.findall(r"[A-Za-zÀ-ÿ0-9]{%d,}" % MIN_TOKEN, str(value)):
            token = token.lower()
            # Un jeton contenant des chiffres est un numero de compte ou un nom
            # de fichier horodate: jamais du vocabulaire courant, toujours
            # signale. Les jetons purement alphabetiques passent par la liste.
            if any(c.isdigit() for c in token) or token not in GENERIC:
                needles.add(token)
    return needles


def main() -> int:
    files, source = files_to_check()
    if not files:
        print("Aucun fichier suivi par git : rien ne peut partir.")
        return 0

    needles = needles_from_database()
    problems = []
    binaries = []

    for path in files:
        if is_binary(path, source):
            binaries.append(path)
            continue
        content = content_of(path, source)
        if not content:
            continue
        lowered = content.lower()

        for pattern, label in SHAPES:
            match = pattern.search(content)
            if match:
                # Le fichier d'exemple documente la forme de la clé sans en contenir une.
                if label == "clé API Anthropic" and path.endswith(".env.example"):
                    continue
                problems.append((path, label, match.group(0)[:24]))

        for needle in needles:
            if needle in lowered:
                problems.append((path, "identifiant présent dans la base locale", needle))

    portee = "indexé(s)" if source == "index" else "suivi(s) par git"
    if binaries:
        print("Fichiers binaires non lisibles automatiquement — à vérifier à l'œil :")
        for path in binaries:
            print(f"  {path}")
        print()
    if not problems:
        print(f"{len(files)} fichier(s) {portee} — aucune donnée personnelle détectée.")
        return 0

    print("DONNÉES PERSONNELLES DÉTECTÉES DANS L'INDEX :\n")
    for path, label, extrait in problems:
        print(f"  {path}\n      {label} : {extrait!r}")
    print(
        "\nRetire ces fichiers avant de pousser :\n"
        "  git rm --cached <fichier>   puis ajoute-le au .gitignore\n"
        "Un push est irréversible : le contenu resterait dans l'historique."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
