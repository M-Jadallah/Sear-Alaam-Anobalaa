"""A normal production shell must never create human-approved QA labels."""
import pytest


def test_mutating_container_probe_refuses_unmarked_production_environment(monkeypatch):
    from backend import production_probe
    monkeypatch.delenv("SIYAR_DISPOSABLE_QA", raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail("The unmarked acceptance probe contacted the application")
    monkeypatch.setattr(production_probe, "request", forbidden)
    with pytest.raises(RuntimeError, match="disposable"):
        production_probe.main()
