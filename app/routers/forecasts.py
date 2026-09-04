import calendar
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.models import CATEGORIES, Forecast
from app.services.forecasting import (
    actual_for,
    actual_over_forecasts,
    existing_overlaps,
    find_overlaps,
)
from app.services.period import MONTH_ABBR_FR, MONTH_NAMES_FR

router = APIRouter(prefix="/forecasts", tags=["forecasts"])
templates = Jinja2Templates(directory="app/templates")


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _add_months(d: date, n: int) -> date:
    """Premier jour du mois situe n mois avant/apres celui de d."""
    total = (d.year * 12 + d.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def _months_between(start: date, end: date) -> list:
    """Liste des premiers jours de mois couvrant [start, end]."""
    months, cursor = [], _month_start(start)
    while cursor <= end:
        months.append(cursor)
        cursor = _month_start(cursor + timedelta(days=32))
    return months


@router.get("")
def list_forecasts(
    request: Request,
    category: Optional[str] = None,
    session: Session = Depends(get_session),
):
    all_forecasts = session.exec(select(Forecast).order_by(Forecast.period_start)).all()
    shown = [f for f in all_forecasts if not category or f.category == category]

    today = date.today()
    rows = []
    total_planned = 0.0
    over_budget = []

    if shown:
        first = _month_start(min(f.period_start for f in shown))
        last = _month_end(max(f.period_end for f in shown))
        # La frise deborde volontairement des previsions: un mois avant et trois
        # apres, pour qu'il y ait toujours de la place a faire defiler autour.
        timeline_start = _add_months(first, -1)
        timeline_end = _month_end(_add_months(last, 3))
        span_days = (timeline_end - timeline_start).days + 1
        months = _months_between(timeline_start, timeline_end)

        for f in shown:
            # "Reel a ce jour": on ne compte que la partie ecoulee de la periode,
            # sinon une prevision qui court encore paraitrait toujours sous-consommee.
            actual = actual_for(session, f.category, f.period_start, min(f.period_end, today))
            planned = f.amount_planned
            total_planned += planned
            if actual > planned:
                over_budget.append({"category": f.category, "excess": round(actual - planned, 2)})

            rows.append(
                {
                    "forecast": f,
                    "actual": actual,
                    "planned": planned,
                    "over": actual > planned,
                    "fill_pct": min(round(actual / planned * 100, 1), 100) if planned else 0,
                    # Positions en JOURS: le gabarit les multiplie par une largeur de
                    # jour reglable en CSS, ce qui rend la frise zoomable sans avoir
                    # a recalculer quoi que ce soit cote serveur.
                    "offset_days": (f.period_start - timeline_start).days,
                    "length_days": (f.period_end - f.period_start).days + 1,
                }
            )

        month_marks = [
            {
                "label": MONTH_ABBR_FR[m.month - 1],
                "year": m.year,
                "offset_days": (m - timeline_start).days,
                "is_current": m.year == today.year and m.month == today.month,
            }
            for m in months
        ]
        today_offset = (today - timeline_start).days if timeline_start <= today <= timeline_end else None
        range_label = (
            f"{MONTH_NAMES_FR[first.month - 1].lower()} \u2192 "
            f"{MONTH_NAMES_FR[last.month - 1].lower()} {last.year}"
        )
    else:
        month_marks, today_offset, range_label, span_days = [], None, "", 0

    # Le total ne peut pas etre la somme des barres: si deux previsions d'une meme
    # categorie se chevauchaient (donnees anterieures a la regle), la depense commune
    # y serait comptee deux fois. On somme sur l'union des periodes.
    total_actual = actual_over_forecasts(session, shown, today)

    return templates.TemplateResponse(
        "forecasts.html",
        {
            "request": request,
            "rows": rows,
            "month_marks": month_marks,
            "today_offset": today_offset,
            "span_days": span_days,
            "range_label": range_label,
            "count": len(shown),
            "total_planned": round(total_planned, 2),
            "total_actual": total_actual,
            "conflicts": existing_overlaps(all_forecasts),
            "over_budget": over_budget,
            "used_categories": sorted({f.category for f in all_forecasts}),
            "filter_category": category or "",
        },
    )


def _form_page(request, session, error, forecast_id, category, start, end, amount):
    """Rendu du formulaire, partage entre l'ouverture et le renvoi apres erreur:
    la saisie est toujours re-affichee telle quelle plutot que perdue."""
    today = date.today()
    calendar_anchor = today
    try:
        calendar_anchor = date.fromisoformat(start)
    except ValueError:
        pass
    return templates.TemplateResponse(
        "forecast_new.html",
        {
            "request": request,
            "categories": CATEGORIES,
            "forecast_id": forecast_id,
            "selected_category": category or "",
            "start": start or "",
            "end": end or "",
            "amount": amount if amount is not None else "",
            "calendar_year": calendar_anchor.year,
            "calendar_month": calendar_anchor.month,
            "today": today.isoformat(),
            "month_names": MONTH_NAMES_FR,
            "error": error,
        },
    )


@router.get("/new")
def new_forecast(
    request: Request,
    category: Optional[str] = None,
    id: Optional[int] = None,
    session: Session = Depends(get_session),
):
    existing = session.get(Forecast, id) if id else None
    return _form_page(
        request, session, None,
        forecast_id=existing.id if existing else None,
        category=existing.category if existing else category,
        start=existing.period_start.isoformat() if existing else "",
        end=existing.period_end.isoformat() if existing else "",
        amount=existing.amount_planned if existing else None,
    )


def _validate_and_save(
    session: Session,
    forecast_id: str,
    category: str,
    start: str,
    end: str,
    amount_planned: float,
):
    """Valide puis enregistre une prevision. Renvoie (prevision, erreur).

    Unique chemin d'ecriture, partage par l'enregistrement automatique (JSON) et
    par la soumission classique du formulaire: la regle de non-chevauchement ne
    peut donc pas etre respectee d'un cote et oubliee de l'autre."""
    # forecast_id vient d'un champ cache: vide en creation, d'ou le str puis la
    # conversion manuelle (meme motif que les <select> "Tous").
    fid = int(forecast_id) if forecast_id else None

    try:
        period_start = date.fromisoformat(start)
        period_end = date.fromisoformat(end)
    except ValueError:
        return None, "Choisis un jour de d\u00e9but puis un jour de fin dans le calendrier."

    if period_start > period_end:
        return None, "La date de fin doit venir apr\u00e8s la date de d\u00e9but."
    if amount_planned <= 0:
        return None, "Le montant pr\u00e9vu doit \u00eatre sup\u00e9rieur \u00e0 0."

    # Regle metier: pas deux previsions d'une meme categorie sur des periodes
    # qui se recouvrent, sinon une depense tomberait dans les deux.
    conflicts = find_overlaps(session, category, period_start, period_end, exclude_id=fid)
    if conflicts:
        c = conflicts[0]
        return None, (
            f"Cette p\u00e9riode chevauche une pr\u00e9vision {category} existante "
            f"({c.period_start.strftime('%d/%m/%Y')} \u2013 {c.period_end.strftime('%d/%m/%Y')}, "
            f"{c.amount_planned:.2f} \u20ac). Deux pr\u00e9visions d'une m\u00eame cat\u00e9gorie ne peuvent pas "
            "se chevaucher : raccourcis l'une des deux, ou modifie celle qui existe d\u00e9j\u00e0."
        )

    forecast = session.get(Forecast, fid) if fid else None
    if forecast is None:
        forecast = Forecast(
            category=category,
            period_start=period_start,
            period_end=period_end,
            amount_planned=amount_planned,
        )
    else:
        forecast.category = category
        forecast.period_start = period_start
        forecast.period_end = period_end
        forecast.amount_planned = amount_planned
    session.add(forecast)
    session.commit()
    session.refresh(forecast)
    return forecast, None


@router.post("/save")
def autosave_forecast(
    category: str = Form(...),
    start: str = Form(""),
    end: str = Form(""),
    amount_planned: float = Form(...),
    forecast_id: str = Form(""),
    session: Session = Depends(get_session),
):
    """Enregistrement automatique: le formulaire n'a plus de bouton, chaque
    modification arrive ici des qu'elle est complete. Renvoie l'identifiant pour
    que les modifications suivantes mettent a jour la meme prevision au lieu
    d'en creer une nouvelle a chaque frappe."""
    forecast, error = _validate_and_save(
        session, forecast_id, category, start, end, amount_planned
    )
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=422)
    return JSONResponse(
        {"ok": True, "id": forecast.id, "saved_at": datetime.now().strftime("%H:%M:%S")}
    )


@router.post("")
def save_forecast(
    request: Request,
    category: str = Form(...),
    start: str = Form(""),
    end: str = Form(""),
    amount_planned: float = Form(...),
    forecast_id: str = Form(""),
    session: Session = Depends(get_session),
):
    """Soumission classique du formulaire (touche Entree, ou navigateur sans
    JavaScript). L'enregistrement automatique passe par /forecasts/save."""
    forecast, error = _validate_and_save(
        session, forecast_id, category, start, end, amount_planned
    )
    if error:
        fid = int(forecast_id) if forecast_id else None
        return _form_page(request, session, error, fid, category, start, end, amount_planned)
    return RedirectResponse(url="/forecasts", status_code=303)


@router.post("/{forecast_id}/delete")
def delete_forecast(forecast_id: int, session: Session = Depends(get_session)):
    forecast = session.get(Forecast, forecast_id)
    if forecast:
        session.delete(forecast)
        session.commit()
    return RedirectResponse(url="/forecasts", status_code=303)
