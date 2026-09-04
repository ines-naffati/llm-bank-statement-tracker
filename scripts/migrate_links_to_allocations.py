"""Convertit les anciennes liaisons 1-1 en affectations de remboursement.

L'ancien mecanisme (`Transaction.linked_transaction_id`) associait exactement deux
transactions sans montant: le net etait deduit de la somme des deux. Le nouveau
(`RefundAllocation`) porte un montant explicite et accepte le N-N.

Chaque paire (credit, debit) devient une affectation de min(credit, debit): c'est
la seule valeur qui conserve le net d'origine sans jamais faire passer une depense
en negatif.

Idempotent: une paire deja convertie est ignoree.

Usage:  python -m scripts.migrate_links_to_allocations
"""

import sqlite3

from sqlmodel import Session, select

from app.db import DB_PATH, engine, init_db
from app.models import RefundAllocation, Transaction

LEGACY_COLUMN = "linked_transaction_id"


def _legacy_pairs(conn: sqlite3.Connection) -> list:
    """Lit les paires via sqlite3 brut: le champ n'existe plus dans le modele."""
    cols = [r[1] for r in conn.execute('PRAGMA table_info("transaction")')]
    if LEGACY_COLUMN not in cols:
        return []
    rows = conn.execute(
        f'SELECT id, {LEGACY_COLUMN}, amount, direction FROM "transaction" '
        f"WHERE {LEGACY_COLUMN} IS NOT NULL"
    ).fetchall()
    by_id = {r[0]: r for r in rows}

    pairs, seen = [], set()
    for tx_id, partner_id, _, _ in rows:
        if tx_id in seen or partner_id in seen or partner_id not in by_id:
            continue
        a, b = by_id[tx_id], by_id[partner_id]
        credit = a if a[3] == "credit" else b
        debit = b if a[3] == "credit" else a
        if credit[3] != "credit" or debit[3] != "debit":
            print(f"  paire ignoree (pas un couple credit/debit): {tx_id} <-> {partner_id}")
            continue
        seen.add(tx_id)
        seen.add(partner_id)
        pairs.append((credit, debit))
    return pairs


def migrate() -> None:
    init_db()  # cree la table refundallocation si absente
    conn = sqlite3.connect(DB_PATH)
    try:
        pairs = _legacy_pairs(conn)
    finally:
        conn.close()

    if not pairs:
        print("Aucune liaison 1-1 a convertir.")
    else:
        with Session(engine) as session:
            for credit, debit in pairs:
                credit_id, debit_id = credit[0], debit[0]
                existing = session.exec(
                    select(RefundAllocation).where(
                        RefundAllocation.refund_transaction_id == credit_id,
                        RefundAllocation.expense_transaction_id == debit_id,
                    )
                ).first()
                if existing:
                    print(f"  deja convertie: {credit_id} -> {debit_id}")
                    continue
                amount = round(min(credit[2], debit[2]), 2)
                session.add(
                    RefundAllocation(
                        refund_transaction_id=credit_id,
                        expense_transaction_id=debit_id,
                        amount=amount,
                    )
                )
                print(
                    f"  +{credit[2]:.2f} € (id {credit_id}) -> -{debit[2]:.2f} € "
                    f"(id {debit_id}) : affectation de {amount:.2f} €"
                )
            session.commit()

    # La colonne devenue inutile est retiree pour qu'il ne reste qu'une seule
    # source de verite. DROP COLUMN demande SQLite >= 3.35.
    conn = sqlite3.connect(DB_PATH)
    try:
        cols = [r[1] for r in conn.execute('PRAGMA table_info("transaction")')]
        if LEGACY_COLUMN not in cols:
            print(f"Colonne {LEGACY_COLUMN} deja absente.")
            return
        try:
            conn.execute(f'ALTER TABLE "transaction" DROP COLUMN {LEGACY_COLUMN}')
            conn.commit()
            print(f"Colonne {LEGACY_COLUMN} supprimee.")
        except sqlite3.OperationalError as exc:
            print(
                f"Colonne {LEGACY_COLUMN} conservee ({exc}). Sans importance: elle "
                "n'est plus lue par l'application. Version de SQLite: "
                f"{sqlite3.sqlite_version} (DROP COLUMN demande 3.35+)."
            )
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
