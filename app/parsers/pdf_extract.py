import pdfplumber


def extract_text(pdf_path: str) -> str:
    """Texte de lecture (lisible, avec espaces) + tableaux bruts en colonnes
    (Débit/Crédit distincts) ajoutés en complément pour lever toute ambiguïté
    sur le sens d'une opération quand le texte à plat seul est trompeur."""
    pages_text = []
    tables_dump = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # x_tolerance serre: certains relevés (ex: Boursorama) utilisent une police dont
            # l'espacement par défaut de pdfplumber colle les mots entre eux ("VIRRemboursement...").
            text = page.extract_text(x_tolerance=1) or ""
            pages_text.append(text)
            for table in page.extract_tables(table_settings={"text_x_tolerance": 1}):
                rows = [
                    " | ".join(cell.replace("\n", " ").strip() if cell else "" for cell in row)
                    for row in table
                ]
                tables_dump.append("\n".join(rows))

    result = "\n\n".join(pages_text)
    if tables_dump:
        result += (
            "\n\n=== TABLEAUX BRUTS EN COLONNES (source de verite pour Débit vs Crédit "
            "en cas de doute sur le texte ci-dessus) ===\n\n" + "\n\n".join(tables_dump)
        )
    return result
