"""Uvicorn entrypoint. `uvicorn app.main:app` from the api directory."""

from app.factory import create_app

app = create_app()
