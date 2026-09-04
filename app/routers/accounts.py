from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.models import Account

router = APIRouter(prefix="/accounts", tags=["accounts"])
templates = Jinja2Templates(directory="app/templates")


@router.get("")
def list_accounts(request: Request, session: Session = Depends(get_session)):
    accounts = session.exec(select(Account).order_by(Account.bank, Account.name)).all()
    return templates.TemplateResponse(
        "accounts.html", {"request": request, "accounts": accounts}
    )


@router.post("")
def create_account(
    name: str = Form(...),
    bank: str = Form(...),
    account_type: str = Form("courant"),
    session: Session = Depends(get_session),
):
    account = Account(name=name, bank=bank, account_type=account_type)
    session.add(account)
    session.commit()
    return RedirectResponse(url="/accounts", status_code=303)
