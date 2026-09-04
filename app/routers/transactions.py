from datetime import datetime
from typing import Optional
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.models import CATEGORIES, Account, Statement, Transaction
from app.services.allocations import (
    allocated_per_expense,
    allocated_per_refund,
    allocations_of_refund,
    allocations_on_expense,
    can_be_refund,
    clear_allocations,
    effective_amount,
    remaining_on_expense,
    save_allocations,
)
from app.services.categorize import learn_category
from app.services.forecasting import actual_by_category, planned_by_category
from app.services.period import resolve_period

router = APIRouter(tags=["transactions"])
templates = Jinja2Templates(directory="app/templates")

PERIOD_COOKIE = "period"
PERIOD_COOKIE_MAX_AGE = 60 * 60 * 24 * 180  # 6 mois


def _period_context(request, period_type, month, year, start, end):
    """Resout la periode consultee. Si l'URL ne porte aucun parametre de periode
    (ex: on vient de cliquer sur un autre onglet), on reprend la derniere periode
    choisie via le cookie plutot que de retomber sur le mois courant."""
    if not any(k in request.query_params for k in ("period_type", "month", "year", "start", "end")):
        cookie_val = request.cookies.get(PERIOD_COOKIE)
        if cookie_val:
            saved = dict(parse_qsl(cookie_val))
            period_type = saved.get("period_type", period_type)
            month = saved.get("month", month)
            year = saved.get("year", year)
            start = saved.get("start", start)
            end = saved.get("end", end)

    view_start, view_end, period_label = resolve_period(period_type, month, year, start, end)
    context = {
        "period_type": period_type,
        "period_label": period_label,
        "month": month or view_start.strftime("%Y-%m"),
        "year": year or str(view_start.year),
        "start": start or view_start.isoformat(),
        "end": end or view_end.isoformat(),
    }
    return view_start, view_end, context


def _remember_period(response, period_ctx):
    cookie_val = urlencode(
        {k: period_ctx[k] for k in ("period_type", "month", "year", "start", "end")}
    )
    response.set_cookie(PERIOD_COOKIE, cookie_val, max_age=PERIOD_COOKIE_MAX_AGE)


@router.get("/")
def home(
    request: Request,
    period_type: str = "month",
    month: Optional[str] = None,
    year: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    session: Session = Depends(get_session),
):
    view_start, view_end, period_ctx = _period_context(request, period_type, month, year, start, end)

    all_transactions = session.exec(select(Transaction)).all()
    period_txs = [t for t in all_transactions if view_start <= t.date <= view_end]

    # Les affectations sont calculees sur TOUTE la base, pas sur la periode: une
    # depense de juin remboursee en juillet affiche donc son montant net meme quand
    # on ne regarde que juin. Le montant effectif d'une transaction ne depend jamais
    # de la fenetre consultee.
    alloc_refund = allocated_per_refund(session)

    # Depenses par categorie: on passe par le service des previsions, qui est aussi
    # celui qu'utilise la page Previsions. Les deux pages ne peuvent donc pas
    # afficher deux "reels" differents pour une meme periode.
    expense_by_category = actual_by_category(session, view_start, view_end)
    expense_total = round(sum(expense_by_category.values()), 2)

    income_total = 0.0
    for t in period_txs:
        if t.is_internal_transfer or t.direction == "debit":
            continue
        # Revenu diminue de ce qui a ete affecte a des depenses: la part affectee
        # est deja deduite cote depenses, la compter ici la compterait deux fois.
        income_total += effective_amount(t, alloc_refund)

    # Meme remarque pour le prevu: une seule implementation, partagee.
    forecast_by_category = planned_by_category(session, view_start, view_end)

    categories = sorted(set(expense_by_category) | {c for c, v in forecast_by_category.items() if v})
    comparison_rows = []
    for cat in categories:
        actual = round(expense_by_category.get(cat, 0.0), 2)
        planned = round(forecast_by_category.get(cat, 0.0), 2)
        diff = round(planned - actual, 2)
        comparison_rows.append({"category": cat, "actual": actual, "planned": planned, "diff": diff})

    accounts = session.exec(select(Account)).all()
    account_balances = []
    for account in accounts:
        last_statement = session.exec(
            select(Statement)
            .where(Statement.account_id == account.id)
            .order_by(Statement.uploaded_at.desc())
        ).first()
        account_balances.append(
            {
                "account": account,
                "balance": last_statement.closing_balance if last_statement else None,
                "as_of": last_statement.period_end if last_statement else None,
                "needs_review": bool(last_statement and not last_statement.reconciliation_ok),
            }
        )

    unreviewed_statements = session.exec(
        select(Statement).where(Statement.reconciliation_ok == False)  # noqa: E712
    ).all()

    response = templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            **period_ctx,
            "income_total": round(income_total, 2),
            "expense_total": round(expense_total, 2),
            "comparison_rows": comparison_rows,
            "account_balances": account_balances,
            "unreviewed_statements": unreviewed_statements,
        },
    )
    _remember_period(response, period_ctx)
    return response


@router.get("/transactions")
def list_transactions(
    request: Request,
    period_type: str = "month",
    month: Optional[str] = None,
    year: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    account_id: Optional[str] = None,
    category: Optional[str] = None,
    internal: Optional[str] = None,
    session: Session = Depends(get_session),
):
    # account_id vient d'un <select> HTML: l'option "Tous" soumet une chaine vide,
    # que FastAPI ne peut pas convertir directement en Optional[int].
    account_id_int = int(account_id) if account_id else None

    view_start, view_end, period_ctx = _period_context(request, period_type, month, year, start, end)

    query = select(Transaction).order_by(Transaction.date.desc())
    if account_id_int:
        query = query.where(Transaction.account_id == account_id_int)
    if category:
        query = query.where(Transaction.category == category)
    # Meme remarque que pour account_id: le <select> "Tous" soumet une chaine vide,
    # d'ou un Optional[str] plutot qu'un Optional[bool].
    if internal == "1":
        query = query.where(Transaction.is_internal_transfer == True)  # noqa: E712
    elif internal == "0":
        query = query.where(Transaction.is_internal_transfer == False)  # noqa: E712
    all_matching = session.exec(query).all()
    transactions = [t for t in all_matching if view_start <= t.date <= view_end]

    alloc_expense = allocated_per_expense(session)
    alloc_refund = allocated_per_refund(session)
    # Montant apres remboursement + nombre de remboursements par depense, pour la
    # colonne "Apres remb." et le detail affiche sous chaque ligne.
    effective_by_id = {}
    refund_count_by_expense = {}
    for t in transactions:
        allocated = alloc_refund if t.direction == "credit" else alloc_expense
        effective_by_id[t.id] = effective_amount(t, allocated)
        if t.direction == "debit" and alloc_expense.get(t.id):
            refund_count_by_expense[t.id] = len(allocations_on_expense(session, t.id))

    accounts = session.exec(select(Account)).all()
    response = templates.TemplateResponse(
        "transactions.html",
        {
            "request": request,
            **period_ctx,
            "transactions": transactions,
            "allocated_expense": alloc_expense,
            "allocated_refund": alloc_refund,
            "effective_by_id": effective_by_id,
            "refund_count_by_expense": refund_count_by_expense,
            "accounts": {a.id: a for a in accounts},
            "categories": CATEGORIES,
            "filter_account_id": account_id_int,
            "filter_category": category or "",
            "filter_internal": internal or "",
            "current_query": request.url.query,
        },
    )
    _remember_period(response, period_ctx)
    return response


def _back_to_list(query: str) -> str:
    """Renvoie vers la liste en conservant les filtres de colonnes en cours. On ne
    reprend que la query string (jamais une URL complete) pour ne pas ouvrir une
    redirection vers l'exterieur."""
    return f"/transactions?{query}" if query else "/transactions"


def _update_result(wants_json: bool, back_query: str, error: Optional[str] = None):
    """Reponse d'un enregistrement automatique: du JSON quand la page l'a demande
    en arriere-plan (elle affiche alors elle-meme le statut), une redirection
    classique sinon."""
    if wants_json:
        if error:
            return JSONResponse({"ok": False, "error": error}, status_code=422)
        return JSONResponse({"ok": True, "saved_at": datetime.now().strftime("%H:%M:%S")})
    return RedirectResponse(url=_back_to_list(back_query), status_code=303)


@router.post("/transactions/{transaction_id}/update")
def update_transaction(
    request: Request,
    transaction_id: int,
    category: str = Form(...),
    is_internal_transfer: bool = Form(False),
    back_query: str = Form(""),
    session: Session = Depends(get_session),
):
    wants_json = request.headers.get("x-requested-with") == "fetch"

    tx = session.get(Transaction, transaction_id)
    if not tx:
        return _update_result(wants_json, back_query, error="Transaction introuvable.")

    if is_internal_transfer and not tx.is_internal_transfer:
        # Un virement interne est exclu des totaux. S'il participait a une
        # repartition, le montant disparaitrait des revenus sans disparaitre des
        # depenses: l'egalite (revenus - depenses) que garantit `allocations.py`
        # serait rompue. On refuse plutot que de casser les comptes en silence.
        engage = allocations_of_refund(session, tx.id) + allocations_on_expense(session, tx.id)
        if engage:
            return _update_result(
                wants_json,
                back_query,
                error=(
                    "Cette transaction participe à une répartition de remboursement. "
                    "La marquer comme virement interne fausserait les totaux : "
                    "supprime d'abord la répartition."
                ),
            )

    tx.category = category
    tx.is_internal_transfer = is_internal_transfer
    tx.is_manual_override = True
    session.add(tx)
    session.commit()
    if tx.merchant_normalized:
        learn_category(session, tx.merchant_normalized, category)
    return _update_result(wants_json, back_query)



@router.get("/transactions/{transaction_id}/allocate")
def allocate_page(
    transaction_id: int,
    request: Request,
    link_start: Optional[str] = None,
    link_end: Optional[str] = None,
    link_category: Optional[str] = None,
    back_query: str = "",
    session: Session = Depends(get_session),
):
    refund = session.get(Transaction, transaction_id)
    if not refund:
        return RedirectResponse(url="/transactions", status_code=303)
    return _render_allocate(
        request, session, refund, link_start, link_category, link_end, back_query
    )


def _render_allocate(
    request,
    session: Session,
    refund: Transaction,
    link_start,
    link_category,
    link_end,
    back_query: str,
    errors: Optional[list] = None,
    submitted: Optional[dict] = None,
):
    """Construit la page de repartition. Partage entre le GET et le POST invalide,
    pour que la saisie de l'utilisatrice soit re-affichee telle quelle avec les
    erreurs plutot que perdue."""
    already = {a.expense_transaction_id: a.amount for a in allocations_of_refund(session, refund.id)}
    current = submitted if submitted is not None else already

    query = select(Transaction).where(
        Transaction.direction == "debit",
        Transaction.is_internal_transfer == False,  # noqa: E712
    ).order_by(Transaction.date.desc())
    if link_category:
        query = query.where(Transaction.category == link_category)
    candidates = session.exec(query).all()
    if link_start:
        candidates = [c for c in candidates if c.date.isoformat() >= link_start]
    if link_end:
        candidates = [c for c in candidates if c.date.isoformat() <= link_end]

    rows = []
    for c in candidates:
        others = [
            a for a in allocations_on_expense(session, c.id)
            if a.refund_transaction_id != refund.id
        ]
        plafond = remaining_on_expense(c, others, excluding_refund_id=refund.id)
        mine = current.get(c.id, 0.0)
        # On masque les depenses deja entierement remboursees par d'autres, sauf si
        # ce remboursement-ci y participe (sinon on ne pourrait plus les corriger).
        if plafond <= 0 and not mine:
            continue
        rows.append(
            {
                "tx": c,
                "account_name": _account_name(session, c.account_id),
                "already_others": round(sum(a.amount for a in others), 2),
                "max_amount": plafond,
                "value": round(mine, 2),
            }
        )
    # Les depenses deja servies par ce remboursement remontent en tete.
    rows.sort(key=lambda r: (r["value"] <= 0, -r["tx"].date.toordinal()))

    allocated_total = round(sum(current.values()), 2)
    return templates.TemplateResponse(
        "allocate.html",
        {
            "request": request,
            "refund": refund,
            "is_valid_refund": can_be_refund(refund),
            "account_name": _account_name(session, refund.account_id),
            "rows": rows,
            "categories": CATEGORIES,
            "link_start": link_start or "",
            "link_end": link_end or "",
            "link_category": link_category or "",
            "allocated_total": allocated_total,
            "remaining_total": round(refund.amount - allocated_total, 2),
            "back_query": back_query,
            "errors": errors or [],
        },
    )


def _account_name(session: Session, account_id: int) -> str:
    account = session.get(Account, account_id)
    return account.name if account else "\u2014"


@router.post("/transactions/{transaction_id}/allocate")
async def allocate_save(
    transaction_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    refund = session.get(Transaction, transaction_id)
    if not refund:
        return RedirectResponse(url="/transactions", status_code=303)

    # Les montants arrivent en champs dynamiques alloc_<id>, un par depense
    # candidate: on lit donc le formulaire brut plutot que des Form(...) nommes.
    form = await request.form()
    wanted = {}
    bad_values = []
    for key, raw in form.items():
        if not key.startswith("alloc_"):
            continue
        raw = (raw or "").strip().replace(",", ".")
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            bad_values.append(raw)
            continue
        if value < 0:
            bad_values.append(raw)
            continue
        if value > 0:
            wanted[int(key[len("alloc_"):])] = value

    back_query = str(form.get("back_query") or "")
    link_start = str(form.get("link_start") or "") or None
    link_end = str(form.get("link_end") or "") or None
    link_category = str(form.get("link_category") or "") or None

    errors = [f"Montant invalide: {v}" for v in bad_values]
    if not errors:
        errors = save_allocations(session, refund, wanted)
    if errors:
        return _render_allocate(
            request, session, refund, link_start, link_category, link_end,
            back_query, errors=errors, submitted=wanted,
        )
    return RedirectResponse(url=_back_to_list(back_query), status_code=303)


@router.post("/transactions/{transaction_id}/allocate/clear")
def allocate_clear(
    transaction_id: int,
    back_query: str = Form(""),
    session: Session = Depends(get_session),
):
    clear_allocations(session, transaction_id)
    return RedirectResponse(url=_back_to_list(back_query), status_code=303)
