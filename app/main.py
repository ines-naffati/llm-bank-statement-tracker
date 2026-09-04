from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.routers import accounts, forecasts, statements, transactions

app = FastAPI(title="Suivi de depenses")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(transactions.router)
app.include_router(accounts.router)
app.include_router(statements.router)
app.include_router(forecasts.router)


@app.on_event("startup")
def on_startup():
    init_db()
