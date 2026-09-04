import json
import os

from anthropic import Anthropic

from app.services.extraction_schema import STATEMENT_JSON_SCHEMA, SYSTEM_PROMPT

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
MAX_ATTEMPTS = 3
# Un releve avec plusieurs sous-comptes (ex: C/C + livrets Credit Mutuel) peut depasser
# largement 8000 tokens de sortie une fois toutes les transactions structurees en JSON;
# une reponse tronquee ne peut pas etre parsee du tout (accounts revient vide).
MAX_TOKENS = 16000

TOOL_SCHEMA = {
    "name": "record_statement",
    "description": "Enregistre les sections de compte et les transactions extraites d'un relevé bancaire.",
    "input_schema": STATEMENT_JSON_SCHEMA,
}


def _call_claude(raw_text: str) -> dict:
    client = Anthropic()
    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=[TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "record_statement"},
        messages=[
            {
                "role": "user",
                "content": f"Voici le texte extrait du relevé bancaire:\n\n{raw_text}",
            }
        ],
    )

    for block in message.content:
        if block.type == "tool_use" and block.name == "record_statement":
            return block.input

    raise RuntimeError("Claude n'a pas retourne de resultat structure (record_statement).")


def _extract_accounts_list(value, depth=0):
    """Recupere la vraie liste de comptes quand Claude renvoie "accounts" sous forme de
    chaine JSON au lieu d'une liste (parfois meme une chaine contenant l'objet complet,
    avec sa propre cle "accounts" imbriquee) - probleme connu avec les schemas d'outil
    fortement imbriques. On deballe recursivement jusqu'a 3 niveaux par securite."""
    if depth > 3:
        return None
    if isinstance(value, list) and all(isinstance(a, dict) for a in value):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return _extract_accounts_list(parsed.get("accounts"), depth + 1)
        return _extract_accounts_list(parsed, depth + 1)
    return None


def extract_statement(raw_text: str) -> dict:
    """Occasionnellement, avec un schema d'outil fortement imbrique, Claude renvoie le
    champ "accounts" sous forme de chaine JSON au lieu d'une vraie liste d'objets. On tente
    de recuperer la structure, sinon on relance l'appel (le probleme est non-deterministe)."""
    last_error = None
    for _ in range(MAX_ATTEMPTS):
        try:
            result = _call_claude(raw_text)
        except Exception as exc:  # erreur reseau/API: on retente aussi
            last_error = exc
            continue

        accounts = _extract_accounts_list(result.get("accounts") if isinstance(result, dict) else None)
        if accounts is not None:
            result["accounts"] = accounts
            return result

        last_error = RuntimeError(f"Reponse Claude mal formee (accounts non structure): {repr(result)[:300]}")

    raise last_error or RuntimeError("Echec de l'extraction Claude apres plusieurs tentatives.")
