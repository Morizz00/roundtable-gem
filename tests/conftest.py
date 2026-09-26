import os

import pytest


@pytest.fixture(autouse=True)
def _hermetic_api_keys(monkeypatch):
    """app.config loads the developer's real .env at import time. Tests must never see (or use) those keys:
    clear every GEMINI_API_KEY* variable before each test; a test that needs a key sets its own."""
    for name in list(os.environ):
        if name.startswith("GEMINI_API_KEY"):
            monkeypatch.delenv(name, raising=False)
