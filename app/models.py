from datetime import UTC, date, datetime
from typing import Optional

from sqlmodel import Field, SQLModel

CATEGORIES = [
    "Alimentation",
    "Transport",
    "Logement",
    "Loisirs",
    "Sante",
    "Shopping",
    "Salaire/Revenus",
    "Epargne/Virement interne",
    "Autres",
]


class Account(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    bank: str
    account_type: str = "courant"
    external_ref: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Statement(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id")
    filename: str
    stored_filename: Optional[str] = None
    file_hash: str = Field(index=True)
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    opening_balance: Optional[float] = None
    closing_balance: Optional[float] = None
    reconciliation_ok: Optional[bool] = None
    reconciliation_diff: Optional[float] = None
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Transaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id")
    statement_id: int = Field(foreign_key="statement.id")
    date: date
    value_date: Optional[date] = None
    description_raw: str
    amount: float
    direction: str  # "debit" | "credit"
    category: str = "Autres"
    merchant_normalized: Optional[str] = None
    is_internal_transfer: bool = False
    is_manual_override: bool = False
    dedup_key: str = Field(index=True)


class RefundAllocation(SQLModel, table=True):
    """Part d'un remboursement (credit) affectee a une depense (debit) precise.

    Relation N-N: un remboursement peut couvrir plusieurs depenses, et une meme
    depense peut etre remboursee par plusieurs credits (ex: deux amis remboursent
    chacun leur part d'un achat commun)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    refund_transaction_id: int = Field(foreign_key="transaction.id", index=True)
    expense_transaction_id: int = Field(foreign_key="transaction.id", index=True)
    amount: float


class MerchantCategoryMap(SQLModel, table=True):
    merchant_normalized: str = Field(primary_key=True)
    category: str


class Forecast(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: Optional[int] = Field(default=None, foreign_key="account.id")
    category: str
    period_start: date
    period_end: date
    amount_planned: float
