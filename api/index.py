from __future__ import annotations
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: F401 - required for Vercel detection

try:
    from backend.main import app
except Exception as e:
    app = FastAPI(title="ScratchFever")

    @app.get("/")
    def _import_error():
        return {"error": str(e)}
