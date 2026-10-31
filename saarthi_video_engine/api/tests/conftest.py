"""Tests never start the real model worker. A test that needs visual evidence injects a fake."""

import pytest


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch):
    monkeypatch.setenv("STUDIO_VISUAL_PROVIDER", "none")
    monkeypatch.setenv("STUDIO_ENGINE_PYTHON", "Z:/no-engine-python-in-tests/python.exe")
