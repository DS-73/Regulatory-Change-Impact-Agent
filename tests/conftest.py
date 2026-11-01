"""Tests never call a real model: provider keys from .env are removed for every test."""
import pytest


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("REGWATCH_PROVIDER", "openrouter")
