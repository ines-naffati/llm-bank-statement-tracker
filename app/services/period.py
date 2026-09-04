import calendar
from datetime import date
from typing import Optional

MONTH_NAMES_FR = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
]

# Abreviations d'usage en francais: elles ne suivent pas une simple troncature
# (on ecrit "juil." et "sept." mais "oct." et "nov.").
MONTH_ABBR_FR = [
    "JANV", "FÉVR", "MARS", "AVR", "MAI", "JUIN",
    "JUIL", "AOÛT", "SEPT", "OCT", "NOV", "DÉC",
]


def resolve_period(
    period_type: str = "month",
    month: Optional[str] = None,
    year: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> tuple[date, date, str]:
    """Retourne (date_debut, date_fin, libelle) pour le type de periode demande.
    Retombe sur le mois courant si les parametres sont absents/invalides."""
    today = date.today()

    if period_type == "year":
        y = int(year) if year else today.year
        return date(y, 1, 1), date(y, 12, 31), str(y)

    if period_type == "custom" and start and end:
        try:
            s = date.fromisoformat(start)
            e = date.fromisoformat(end)
            if s <= e:
                return s, e, f"{s.strftime('%d/%m/%Y')} – {e.strftime('%d/%m/%Y')}"
        except ValueError:
            pass
        # parametres personnalises invalides: retombe sur le mois courant

    m = month or today.strftime("%Y-%m")
    try:
        y, mo = (int(part) for part in m.split("-"))
    except ValueError:
        y, mo = today.year, today.month
    last_day = calendar.monthrange(y, mo)[1]
    label = f"{MONTH_NAMES_FR[mo - 1]} {y}"
    return date(y, mo, 1), date(y, mo, last_day), label


def prorate(amount_planned: float, period_start: date, period_end: date, view_start: date, view_end: date) -> float:
    """Part du montant prevu couverte par le chevauchement entre la periode de la
    prevision et la periode consultee, au prorata du nombre de jours."""
    overlap_start = max(period_start, view_start)
    overlap_end = min(period_end, view_end)
    if overlap_start > overlap_end:
        return 0.0
    overlap_days = (overlap_end - overlap_start).days + 1
    total_days = (period_end - period_start).days + 1
    if total_days <= 0:
        return 0.0
    return amount_planned * overlap_days / total_days
