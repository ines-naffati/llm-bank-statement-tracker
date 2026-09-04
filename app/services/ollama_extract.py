import json
import os

import ollama

from app.services.extraction_schema import STATEMENT_JSON_SCHEMA, SYSTEM_PROMPT

MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


def extract_statement(raw_text: str) -> dict:
    client = ollama.Client(host=HOST)
    response = client.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Voici le texte extrait du relevé bancaire:\n\n{raw_text}"},
        ],
        format=STATEMENT_JSON_SCHEMA,
        options={"temperature": 0},
    )
    content = response["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ollama n'a pas retourne de JSON valide: {exc}\nContenu: {content[:500]}") from exc
