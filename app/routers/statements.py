import shutil
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import UPLOADS_DIR, get_session
from app.models import Account, Statement
from app.services.import_statement import import_pdf

router = APIRouter(prefix="/statements", tags=["statements"])
templates = Jinja2Templates(directory="app/templates")


def _parse_date(value: Optional[str]):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _filtered_statements(session: Session, account_id: Optional[str], start: Optional[str], end: Optional[str]):
    account_id_int = int(account_id) if account_id else None
    start_date = _parse_date(start)
    end_date = _parse_date(end)

    query = select(Statement).order_by(Statement.uploaded_at.desc())
    if account_id_int:
        query = query.where(Statement.account_id == account_id_int)
    statements = session.exec(query).all()

    if start_date:
        statements = [s for s in statements if s.period_end is None or s.period_end >= start_date]
    if end_date:
        statements = [s for s in statements if s.period_start is None or s.period_start <= end_date]

    return statements, account_id_int


@router.get("")
def list_statements(
    request: Request,
    account_id: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    session: Session = Depends(get_session),
):
    statements, account_id_int = _filtered_statements(session, account_id, start, end)
    accounts = {a.id: a for a in session.exec(select(Account)).all()}
    return templates.TemplateResponse(
        "statements.html",
        {
            "request": request,
            "statements": statements,
            "accounts": accounts,
            "results": None,
            "filter_account_id": account_id_int,
            "filter_start": start or "",
            "filter_end": end or "",
        },
    )


@router.post("/upload")
def upload_statement(
    request: Request,
    file: UploadFile,
    session: Session = Depends(get_session),
):
    dest_path = UPLOADS_DIR / f"{uuid.uuid4().hex}_{file.filename}"
    with open(dest_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    try:
        results = import_pdf(session, str(dest_path), file.filename)
        error = None
    except Exception as exc:  # surfaced to the user rather than a raw 500
        results = None
        error = str(exc)

    statements, _ = _filtered_statements(session, None, None, None)
    accounts = {a.id: a for a in session.exec(select(Account)).all()}
    return templates.TemplateResponse(
        "statements.html",
        {
            "request": request,
            "statements": statements,
            "accounts": accounts,
            "results": results,
            "error": error,
            "filter_account_id": None,
            "filter_start": "",
            "filter_end": "",
        },
    )


@router.get("/{statement_id}/file")
def download_statement_file(statement_id: int, session: Session = Depends(get_session)):
    statement = session.get(Statement, statement_id)
    if not statement or not statement.stored_filename:
        raise HTTPException(status_code=404, detail="Fichier introuvable pour ce relevé.")

    path = UPLOADS_DIR / statement.stored_filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable sur le disque.")

    return FileResponse(
        path,
        media_type="application/pdf",
        filename=statement.filename,
        content_disposition_type="inline",
    )
