from typing import Optional

from sqlmodel import Session, select

from app.models import MerchantCategoryMap


def lookup_learned_category(session: Session, merchant_normalized: str) -> Optional[str]:
    if not merchant_normalized:
        return None
    row = session.get(MerchantCategoryMap, merchant_normalized)
    return row.category if row else None


def learn_category(session: Session, merchant_normalized: str, category: str) -> None:
    if not merchant_normalized:
        return
    row = session.get(MerchantCategoryMap, merchant_normalized)
    if row:
        row.category = category
    else:
        row = MerchantCategoryMap(merchant_normalized=merchant_normalized, category=category)
    session.add(row)
    session.commit()


def resolve_category(session: Session, merchant_normalized: str, claude_category: str) -> str:
    learned = lookup_learned_category(session, merchant_normalized)
    return learned or claude_category
