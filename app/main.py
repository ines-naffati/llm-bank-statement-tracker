import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.routers import accounts, forecasts, statements, transactions
from app.services.backup import note_change

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    # Instantane d'ouverture: fige l'etat "avant la session du jour", avant
    # meme la premiere modification. `note_change` plutot que `snapshot` pour
    # ne pas empiler un fichier par redemarrage quand on travaille avec
    # `uvicorn --reload`.
    note_change("demarrage")
    yield


app = FastAPI(title="Suivi de depenses", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(transactions.router)
app.include_router(accounts.router)
app.include_router(statements.router)
app.include_router(forecasts.router)
