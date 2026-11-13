"""Garde-fous autour de l'extraction par LLM: rapprochement des soldes et
recuperation des reponses mal formees."""

import json

from app.services.claude_extract import _extract_accounts_list
from app.services.reconciliation import reconcile


def tx(amount, direction):
    return {"amount": amount, "direction": direction}


def test_reconcile_ok_when_balances_match():
    ok, diff = reconcile(100.0, 150.0, [tx(80, "credit"), tx(30, "debit")])
    assert ok and diff == 0


def test_reconcile_flags_a_missing_transaction():
    # Le modele a oublie un debit de 12.50: le solde recalcule ne tombe plus juste.
    ok, diff = reconcile(100.0, 137.50, [tx(80, "credit"), tx(30, "debit")])
    assert not ok and diff == 12.5


def test_reconcile_flags_a_debit_read_as_credit():
    # Inversion debit/credit: l'erreur la plus frequente des petits modeles.
    ok, diff = reconcile(100.0, 150.0, [tx(80, "credit"), tx(30, "credit")])
    assert not ok and diff == 60


def test_reconcile_tolerates_rounding():
    ok, _ = reconcile(0.0, 0.3, [tx(0.1, "credit"), tx(0.2, "credit")])
    assert ok


def test_accounts_list_passthrough():
    accounts = [{"bank": "Demo", "transactions": []}]
    assert _extract_accounts_list(accounts) == accounts


def test_accounts_list_recovered_from_json_string():
    accounts = [{"bank": "Demo", "transactions": []}]
    assert _extract_accounts_list(json.dumps(accounts)) == accounts


def test_accounts_list_recovered_from_nested_object_string():
    accounts = [{"bank": "Demo", "transactions": []}]
    nested = json.dumps({"accounts": json.dumps(accounts)})
    assert _extract_accounts_list(nested) == accounts


def test_accounts_list_rejects_garbage():
    assert _extract_accounts_list("not json") is None
    assert _extract_accounts_list([1, 2, 3]) is None
