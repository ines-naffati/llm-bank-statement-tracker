"""Source de verite unique des previsions.

Les previsions apparaissent sur deux pages (la frise /forecasts et le tableau
comparatif de la page d'accueil). Ces deux pages DOIVENT toujours afficher les
memes chiffres: elles passent donc obligatoirement par les fonctions de ce module,
et ne recalculent jamais un "prevu" ou un "reel" dans leur coin.

Regle metier: deux previsions d'une meme categorie ne peuvent pas se chevaucher.
Sans elle, une meme depense tomberait dans deux previsions a la fois et serait
comptee deux fois dans les totaux.
"""

from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Tuple

from sqlmodel import Session, select

from app.models import Forecast, Transaction
from app.services.allocations import allocated_per_expense, effective_amount
from app.services.period import prorate


def actual_by_category(session: Session, start: date, end: date) -> Dict[str, float]:
    """Depenses reelles par categorie sur [start, end].

    Montants effectifs (nets des remboursements affectes) et hors virements
    internes. Chaque transaction est parcourue une seule fois, donc comptee une
    seule fois: c'est ce qui garantit qu'aucun euro ne peut etre double."""
    allocated = allocated_per_expense(session)
    totals: Dict[str, float] = defaultdict(float)
    for t in session.exec(
        select(Transaction).where(
            Transaction.direction == "debit",
            Transaction.is_internal_transfer == False,  # noqa: E712
        )
    ).all():
        if start <= t.date <= end:
            totals[t.category] += effective_amount(t, allocated)
    return {k: round(v, 2) for k, v in totals.items()}


def actual_for(session: Session, category: str, start: date, end: date) -> float:
    if end < start:
        return 0.0
    return actual_by_category(session, start, end).get(category, 0.0)


def planned_by_category(session: Session, view_start: date, view_end: date) -> Dict[str, float]:
    """Montants prevus par categorie sur la periode consultee, au prorata des jours.

    Une prevision qui deborde de la periode consultee n'y contribue qu'au prorata
    de son chevauchement (une prevision semestrielle vue sur un mois compte pour
    environ un sixieme)."""
    totals: Dict[str, float] = defaultdict(float)
    for f in session.exec(select(Forecast)).all():
        totals[f.category] += prorate(f.amount_planned, f.period_start, f.period_end, view_start, view_end)
    return {k: round(v, 2) for k, v in totals.items()}


def find_overlaps(
    session: Session,
    category: str,
    start: date,
    end: date,
    exclude_id: Optional[int] = None,
) -> List[Forecast]:
    """Previsions de la meme categorie qui recouvriraient [start, end].

    `exclude_id` sert lors d'une modification: une prevision ne se chevauche pas
    elle-meme."""
    conflicts = []
    for f in session.exec(select(Forecast).where(Forecast.category == category)).all():
        if exclude_id is not None and f.id == exclude_id:
            continue
        if f.period_start <= end and start <= f.period_end:
            conflicts.append(f)
    return conflicts


def _merge_intervals(intervals: List[Tuple[date, date]]) -> List[Tuple[date, date]]:
    """Fusionne des intervalles de dates qui se touchent ou se recouvrent."""
    merged: List[Tuple[date, date]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def actual_over_forecasts(session: Session, forecasts: List[Forecast], until: date) -> float:
    """Total reel couvert par un ensemble de previsions, jusqu'a `until`.

    On fusionne les periodes categorie par categorie avant de sommer: si deux
    previsions d'une meme categorie se chevauchaient malgre tout (donnees
    anterieures a la regle), la depense commune ne serait comptee qu'une fois."""
    by_category: Dict[str, List[Tuple[date, date]]] = defaultdict(list)
    for f in forecasts:
        end = min(f.period_end, until)
        if end >= f.period_start:
            by_category[f.category].append((f.period_start, end))

    total = 0.0
    for category, intervals in by_category.items():
        for start, end in _merge_intervals(intervals):
            total += actual_for(session, category, start, end)
    return round(total, 2)


def existing_overlaps(forecasts: List[Forecast]) -> List[Tuple[Forecast, Forecast]]:
    """Paires en infraction deja presentes en base (creees avant la regle)."""
    pairs = []
    by_category: Dict[str, List[Forecast]] = defaultdict(list)
    for f in forecasts:
        by_category[f.category].append(f)
    for items in by_category.values():
        items = sorted(items, key=lambda f: f.period_start)
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if b.period_start <= a.period_end:
                    pairs.append((a, b))
    return pairs
