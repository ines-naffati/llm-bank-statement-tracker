import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from app.models import Account, Statement, Transaction
from app.parsers.pdf_extract import extract_text
from app.services.categorize import resolve_category
from app.services.reconciliation import reconcile

_BACKEND = os.environ.get("EXTRACTION_BACKEND", "claude").lower()
if _BACKEND == "ollama":
    from app.services.ollama_extract import extract_statement
else:
    from app.services.claude_extract import extract_statement


def compute_file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_date(value: Optional[str]):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def find_or_create_account(session: Session, bank: str, label: str, number: str) -> Account:
    account = None
    if number:
        account = session.exec(
            select(Account).where(Account.external_ref == number)
        ).first()
    if not account:
        account = session.exec(
            select(Account).where(Account.bank == bank, Account.name == label)
        ).first()
    if not account:
        account = Account(name=label, bank=bank, external_ref=number or None)
        session.add(account)
        session.commit()
        session.refresh(account)
    return account


def _dedup_key(account_id: int, date_str: str, amount: float, direction: str, description: str) -> str:
    raw = f"{account_id}|{date_str}|{amount}|{direction}|{description[:40]}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def import_pdf(session: Session, file_path: str, original_filename: str) -> list[dict]:
    file_hash = compute_file_hash(file_path)
    existing = session.exec(select(Statement).where(Statement.file_hash == file_hash)).first()
    if existing:
        return [{"already_imported": True, "filename": original_filename}]

    raw_text = extract_text(file_path)
    result = extract_statement(raw_text)
    stored_filename = Path(file_path).name

    summaries = []
    for section in result.get("accounts", []):
        account = find_or_create_account(
            session,
            bank=section.get("bank", "Inconnu"),
            label=section.get("account_label", "Compte"),
            number=section.get("account_number", ""),
        )

        transactions = section.get("transactions", [])
        opening = section.get("opening_balance") or 0.0
        closing = section.get("closing_balance") or 0.0
        ok, diff = reconcile(opening, closing, transactions)

        period_start = _parse_date(section.get("period_start"))
        period_end = _parse_date(section.get("period_end"))
        if period_start is None or period_end is None:
            # Claude n'a pas toujours reussi a lire la periode affichee sur le releve:
            # on la deduit alors des dates de transactions elles-memes (plus ancienne
            # -> plus recente) plutot que de laisser le champ vide.
            tx_dates = sorted(d for d in (_parse_date(t.get("date")) for t in transactions) if d)
            if tx_dates:
                period_start = period_start or tx_dates[0]
                period_end = period_end or tx_dates[-1]

        statement = Statement(
            account_id=account.id,
            filename=original_filename,
            stored_filename=stored_filename,
            file_hash=file_hash,
            period_start=period_start,
            period_end=period_end,
            opening_balance=opening,
            closing_balance=closing,
            reconciliation_ok=ok,
            reconciliation_diff=diff,
        )
        session.add(statement)
        session.commit()
        session.refresh(statement)

        inserted, skipped_dupes = 0, 0
        for t in transactions:
            merchant_norm = (t.get("merchant") or "").strip().upper()
            category = resolve_category(session, merchant_norm, t.get("category", "Autres"))
            date_str = t.get("date")
            tx_date = _parse_date(date_str)
            if not tx_date:
                continue
            dedup_key = _dedup_key(account.id, date_str, t["amount"], t["direction"], t["description"])
            already = session.exec(
                select(Transaction).where(
                    Transaction.account_id == account.id, Transaction.dedup_key == dedup_key
                )
            ).first()
            if already:
                skipped_dupes += 1
                continue

            session.add(
                Transaction(
                    account_id=account.id,
                    statement_id=statement.id,
                    date=tx_date,
                    value_date=_parse_date(t.get("value_date")),
                    description_raw=t["description"],
                    amount=t["amount"],
                    direction=t["direction"],
                    category=category,
                    merchant_normalized=merchant_norm,
                    is_internal_transfer=t.get("is_internal_transfer", False),
                    dedup_key=dedup_key,
                )
            )
            inserted += 1
        session.commit()

        summaries.append(
            {
                "already_imported": False,
                "account_name": account.name,
                "account_id": account.id,
                "statement_id": statement.id,
                "inserted": inserted,
                "skipped_dupes": skipped_dupes,
                "reconciliation_ok": ok,
                "reconciliation_diff": diff,
            }
        )

    return summaries
