"""Cree une base de demonstration entierement fictive (captures d'ecran, essais).

Refuse de s'executer sur le dossier de donnees par defaut, pour ne jamais
melanger des donnees inventees a la vraie base.

Usage:
    SUIVI_DATA_DIR=demo_data python -m scripts.seed_demo
    SUIVI_DATA_DIR=demo_data uvicorn app.main:app
"""

import hashlib
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

if not os.environ.get("SUIVI_DATA_DIR"):
    sys.exit("Definis SUIVI_DATA_DIR vers un dossier de demo (ex: SUIVI_DATA_DIR=demo_data).")

from sqlmodel import Session, SQLModel, select  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import (  # noqa: E402
    Account,
    Forecast,
    MerchantCategoryMap,
    RefundAllocation,
    Statement,
    Transaction,
)
from app.paths import DATA_DIR  # noqa: E402

DEFAULT_DIR = (Path(__file__).resolve().parent.parent / "data").resolve()
if DATA_DIR == DEFAULT_DIR:
    sys.exit("SUIVI_DATA_DIR pointe vers les vraies donnees: choisis un autre dossier.")

rng = random.Random(42)
MONTHS = [(2026, m) for m in (6, 7, 8, 9, 10)]

# (commercant, categorie, montant min, max, nombre par mois)
RECURRING = [
    ("CARREFOUR CITY", "Alimentation", 8, 45, 6),
    ("MONOPRIX", "Alimentation", 12, 60, 3),
    ("BOULANGERIE DU PORT", "Alimentation", 2, 9, 5),
    ("SNCF CONNECT", "Transport", 15, 90, 1),
    ("TAN NANTES", "Transport", 1.7, 1.7, 4),
    ("CINEMA KATORZA", "Loisirs", 8, 12, 1),
    ("DECATHLON", "Shopping", 15, 70, 1),
    ("PHARMACIE CENTRALE", "Sante", 5, 25, 1),
]
FIXED = [
    ("LOYER RESIDENCE ETUDIANTE", "Logement", 520.0, 5),
    ("FREE MOBILE", "Logement", 9.99, 12),
    ("SPOTIFY", "Loisirs", 5.99, 15),
    ("SALLE DE SPORT", "Loisirs", 24.90, 3),
]


def dkey(account_id, d, amount, direction, desc):
    raw = f"{account_id}|{d.isoformat()}|{amount}|{direction}|{desc[:40]}"
    return hashlib.md5(raw.encode()).hexdigest()


def month_days(year, month):
    start = date(year, month, 1)
    end = (date(year + month // 12, month % 12 + 1, 1)) - timedelta(days=1)
    return start, end


def build():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        checking = Account(name="Compte courant", bank="Banque Demo", external_ref="DEMO-0001")
        savings = Account(name="Livret A", bank="Banque Demo", account_type="epargne", external_ref="DEMO-0002")
        s.add_all([checking, savings])
        s.flush()

        balance, savings_balance = 1250.0, 3000.0
        dinner_debit = None
        for i, (y, m) in enumerate(MONTHS):
            start, end = month_days(y, m)
            st = Statement(account_id=checking.id, filename=f"releve_demo_{y}-{m:02d}.pdf",
                           file_hash=f"demo-{y}-{m}", period_start=start, period_end=end,
                           opening_balance=round(balance, 2))
            s.add(st)
            s.flush()
            txs = []

            def add(day, desc, merchant, cat, amount, direction, internal=False):
                txs.append(Transaction(
                    account_id=checking.id, statement_id=st.id, date=date(y, m, day),
                    description_raw=desc, amount=round(amount, 2), direction=direction,
                    category=cat, merchant_normalized=merchant, is_internal_transfer=internal,
                    dedup_key=dkey(checking.id, date(y, m, day), round(amount, 2), direction, desc)))
                return txs[-1]

            add(1, "VIR BOURSE ETUDES", "BOURSE ETUDES", "Salaire/Revenus", 620.0, "credit")
            add(3, "VIR SALAIRE TUTORAT", "TUTORAT", "Salaire/Revenus", rng.uniform(380, 520), "credit")
            for merchant, cat, amount, day in FIXED:
                add(day, f"PRLV SEPA {merchant}", merchant, cat, amount, "debit")
            for merchant, cat, lo, hi, n in RECURRING:
                for _ in range(n):
                    day = rng.randint(1, end.day)
                    add(day, f"CB {merchant} {day:02d}/{m:02d}", merchant, cat, rng.uniform(lo, hi), "debit")
            add(25, "VIR C/C VERS LIVRET A", "LIVRET A", "Epargne/Virement interne", 100.0, "debit", True)
            savings_balance += 100
            if i == 2:  # un diner paye pour des amis, puis rembourse
                dinner_debit = add(14, "CB RESTAURANT LE CAP 14/08", "RESTAURANT LE CAP", "Loisirs", 96.0, "debit")
                refund = add(16, "VIR M. DUPONT REMBOURSEMENT", "DUPONT", "Autres", 48.0, "credit")
            s.add_all(txs)
            s.flush()
            if i == 2:
                s.add(RefundAllocation(refund_transaction_id=refund.id,
                                       expense_transaction_id=dinner_debit.id, amount=48.0))

            credit = sum(t.amount for t in txs if t.direction == "credit")
            debit = sum(t.amount for t in txs if t.direction == "debit")
            balance = balance + credit - debit
            # Le releve de septembre simule une extraction imparfaite: ecart detecte au rapprochement.
            printed = round(balance + (12.50 if (y, m) == (2026, 9) else 0.0), 2)
            st.closing_balance = printed
            st.reconciliation_diff = round(balance - printed, 2)
            st.reconciliation_ok = abs(st.reconciliation_diff) <= 0.01
            balance = printed

        for merchant, cat in {(r[0], r[1]) for r in RECURRING + [(f[0], f[1]) for f in FIXED]}:
            s.merge(MerchantCategoryMap(merchant_normalized=merchant, category=cat))

        budgets = {"Alimentation": 260, "Transport": 70, "Logement": 535, "Loisirs": 90,
                   "Shopping": 50, "Sante": 20}
        for y, m in MONTHS:
            start, end = month_days(y, m)
            for cat, amount in budgets.items():
                s.add(Forecast(category=cat, period_start=start, period_end=end, amount_planned=amount))
        s.commit()
        n = len(s.exec(select(Transaction)).all())
    print(f"Base de demo creee dans {DATA_DIR}: {n} transactions fictives sur {len(MONTHS)} mois.")


if __name__ == "__main__":
    build()
