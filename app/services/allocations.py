"""Affectation d'un remboursement sur une ou plusieurs depenses.

Source de verite unique du "montant effectif" (montant apres remboursement).
Tout ce qui affiche ou totalise un montant net doit passer par ici plutot que de
recalculer dans son coin - sinon la page d'accueil et la liste des transactions
finissent par diverger.

Invariant comptable garanti par les regles de validation ci-dessous:
une affectation de X retire X aux depenses ET X aux revenus, donc
(revenus - depenses) est identique avec ou sans affectation.
"""

from typing import Dict, List

from sqlmodel import Session, select

from app.models import RefundAllocation, Transaction

CENT = 0.005  # tolerance de comparaison sur des flottants en euros


def allocated_per_expense(session: Session) -> Dict[int, float]:
    """{id de la depense: total deja rembourse}, toutes sources confondues."""
    totals: Dict[int, float] = {}
    for a in session.exec(select(RefundAllocation)).all():
        totals[a.expense_transaction_id] = totals.get(a.expense_transaction_id, 0.0) + a.amount
    return totals


def allocated_per_refund(session: Session) -> Dict[int, float]:
    """{id du remboursement: total deja affecte}."""
    totals: Dict[int, float] = {}
    for a in session.exec(select(RefundAllocation)).all():
        totals[a.refund_transaction_id] = totals.get(a.refund_transaction_id, 0.0) + a.amount
    return totals


def allocations_of_refund(session: Session, refund_id: int) -> List[RefundAllocation]:
    return session.exec(
        select(RefundAllocation).where(RefundAllocation.refund_transaction_id == refund_id)
    ).all()


def allocations_on_expense(session: Session, expense_id: int) -> List[RefundAllocation]:
    return session.exec(
        select(RefundAllocation).where(RefundAllocation.expense_transaction_id == expense_id)
    ).all()


def effective_amount(tx: Transaction, allocated: Dict[int, float]) -> float:
    """Montant restant a la charge (debit) ou restant a affecter (credit)."""
    return round(tx.amount - allocated.get(tx.id, 0.0), 2)


def remaining_on_expense(
    expense: Transaction, allocations: List[RefundAllocation], excluding_refund_id: int = None
) -> float:
    """Plafond affectable sur cette depense.

    `excluding_refund_id` sert quand on edite un remboursement deja enregistre:
    sa propre part ne doit pas compter comme "deja pris", sinon on ne pourrait
    plus la modifier. Les parts des AUTRES remboursements, elles, restent
    reservees (cas des deux amis qui remboursent le meme achat)."""
    taken = sum(
        a.amount for a in allocations if a.refund_transaction_id != excluding_refund_id
    )
    return round(max(expense.amount - taken, 0.0), 2)


def can_be_refund(tx: Transaction) -> bool:
    return tx.direction == "credit" and not tx.is_internal_transfer


def can_be_expense(tx: Transaction) -> bool:
    return tx.direction == "debit" and not tx.is_internal_transfer


def save_allocations(
    session: Session, refund: Transaction, wanted: Dict[int, float]
) -> List[str]:
    """Remplace toutes les affectations du remboursement par `wanted`.

    Renvoie la liste des erreurs; si elle n'est pas vide, RIEN n'est ecrit.
    Unique chemin d'ecriture: toute la validation vit ici, jamais dans le
    template ni dans le JS (qui ne sont qu'un confort de saisie)."""
    errors: List[str] = []

    if not can_be_refund(refund):
        if refund.direction != "credit":
            errors.append("Seule une transaction au credit peut servir de remboursement.")
        else:
            errors.append(
                "Un virement interne ne peut pas servir de remboursement: il est deja "
                "exclu des totaux, l'affecter fausserait les comptes."
            )
        return errors

    cleaned: Dict[int, float] = {}
    for expense_id, amount in wanted.items():
        amount = round(amount, 2)
        if amount <= 0:
            continue  # une part vide = pas d'affectation, ce n'est pas une erreur

        expense = session.get(Transaction, expense_id)
        if not expense:
            errors.append(f"Depense introuvable (id {expense_id}).")
            continue
        if not can_be_expense(expense):
            errors.append(
                f"\"{expense.description_raw}\" n'est pas une depense remboursable "
                "(credit ou virement interne)."
            )
            continue

        others = [
            a
            for a in allocations_on_expense(session, expense_id)
            if a.refund_transaction_id != refund.id
        ]
        plafond = remaining_on_expense(expense, others, excluding_refund_id=refund.id)
        if amount - plafond > CENT:
            errors.append(
                f"{amount:.2f} € sur \"{expense.description_raw}\" depasse le maximum "
                f"remboursable ({plafond:.2f} €)."
            )
            continue

        cleaned[expense_id] = amount

    total = round(sum(cleaned.values()), 2)
    if total - refund.amount > CENT:
        errors.append(
            f"Total affecte {total:.2f} € superieur au remboursement "
            f"({refund.amount:.2f} €)."
        )

    if errors:
        return errors

    for existing in allocations_of_refund(session, refund.id):
        session.delete(existing)
    for expense_id, amount in cleaned.items():
        session.add(
            RefundAllocation(
                refund_transaction_id=refund.id,
                expense_transaction_id=expense_id,
                amount=amount,
            )
        )
    session.commit()
    return []


def clear_allocations(session: Session, refund_id: int) -> None:
    for existing in allocations_of_refund(session, refund_id):
        session.delete(existing)
    session.commit()
