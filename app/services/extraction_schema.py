from app.models import CATEGORIES

TRANSACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "date": {"type": "string", "description": "YYYY-MM-DD, date d'operation"},
        "value_date": {"type": "string", "description": "YYYY-MM-DD, date de valeur"},
        "description": {
            "type": "string",
            "description": "Libelle complet, lignes de continuation fusionnees en une seule chaine.",
        },
        "amount": {"type": "number", "description": "Montant positif"},
        "direction": {"type": "string", "enum": ["debit", "credit"]},
        "merchant": {
            "type": "string",
            "description": "Nom du commercant/contrepartie normalise (court, sans numeros de carte/reference).",
        },
        "category": {"type": "string", "enum": CATEGORIES},
        "is_internal_transfer": {
            "type": "boolean",
            "description": "true si virement entre comptes du meme titulaire (ex: VIR C/C <-> Livret).",
        },
    },
    "required": ["date", "description", "amount", "direction", "merchant", "category", "is_internal_transfer"],
}

ACCOUNT_SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "bank": {"type": "string"},
        "account_label": {
            "type": "string",
            "description": "Libelle du compte tel qu'affiche, ex: 'C/C EUROCOMPTE JEUNE'",
        },
        "account_number": {
            "type": "string",
            "description": "Numero de compte tel qu'imprime (chiffres), utilise comme cle de rapprochement.",
        },
        "period_start": {"type": "string", "description": "YYYY-MM-DD"},
        "period_end": {"type": "string", "description": "YYYY-MM-DD"},
        "opening_balance": {"type": "number"},
        "closing_balance": {"type": "number"},
        "transactions": {"type": "array", "items": TRANSACTION_SCHEMA},
    },
    "required": ["bank", "account_label", "account_number", "opening_balance", "closing_balance", "transactions"],
}

# Top-level JSON schema for the whole extraction result (used directly as Ollama's
# structured-output "format", and wrapped into a tool definition for Anthropic).
STATEMENT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "accounts": {
            "type": "array",
            "description": (
                "Un releve peut contenir plusieurs comptes/sous-comptes du meme titulaire "
                "(ex: compte courant + livrets). Une entree par section de compte trouvee dans le document, "
                "en ignorant les blocs qui ne sont pas des tableaux de transactions "
                "(situation de credits, totaux de frais, informations legales)."
            ),
            "items": ACCOUNT_SECTION_SCHEMA,
        }
    },
    "required": ["accounts"],
}

SYSTEM_PROMPT = f"""Tu extrais les donnees structurees d'un relevé bancaire francais (texte brut extrait d'un PDF).

Regles:
- Un document peut contenir plusieurs sections de compte (compte courant, livrets...). Cree une entree "accounts" par section qui a un vrai tableau de transactions avec un solde d'ouverture et de cloture. Ignore les sections "SITUATION DE VOS CREDITS", "TOTAL DES FRAIS", et tout texte legal/publicitaire.
- Convertis toutes les dates au format ISO YYYY-MM-DD (les relevés sont au format JJ/MM/AAAA).
- Certaines lignes d'operation sont reparties sur plusieurs lignes de texte (ex: une ligne avec la date/montant, puis une ligne de continuation avec l'enseigne). Fusionne-les en une seule transaction avec une description complete.
- Les lignes groupees du type "4 PAIEMENTS 1407 PARIS 9" representent plusieurs petits paiements deja agreges par la banque: garde-les comme une seule transaction, ne les decompose pas.
- Le montant "amount" est toujours positif; utilise "direction" pour indiquer debit ou credit. Ne te fie JAMAIS au sens litteral du libelle pour deviner debit/credit (ex: "Virement depuis BoursoBank" ou "Virement depuis X" peut tres bien etre un debit malgre le mot "depuis"). La seule source fiable est la position de chaque montant dans la colonne Debit ou Credit du "TABLEAU BRUT EN COLONNES" fourni en complement: compte le nombre de lignes de transaction et fais correspondre chaque montant a sa position exacte dans la liste Debit ou la liste Credit de ce tableau.
- Categorise chaque transaction avec une seule valeur parmi: {", ".join(CATEGORIES)}.
- Marque is_internal_transfer=true pour les virements entre comptes du meme titulaire (mots-cles: "VIR C/C", "VIR LIVRET", "VIR DE MME/M <nom du titulaire du relevé>" quand ca correspond clairement a un mouvement d'épargne interne, pas a un virement recu d'un tiers). Dans le doute, laisse a false.
- N'invente aucune transaction absente du texte. N'omets aucune transaction presente."""
