from fastapi import FastAPI

app = FastAPI(title="ScratchFever")

# Routes are mounted at startup via backend.main — this file is the Vercel entrypoint only.
# Import the full app lazily so Vercel can detect the entrypoint without running the backend.
try:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from backend.main import app  # noqa: F811
except Exception:
    pass
