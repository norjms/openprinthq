"""The farm interface: the hub reaches its print farm only through clients.farm() (the farm)."""
import pytest


def test_unsupported_raises_an_api_error_callers_already_handle():
    from app.clients.base import ApiError
    from app.clients.farm_base import FarmBackend, FarmUnsupported

    class Tiny(FarmBackend):
        kind = "tiny"
        CAPABILITIES = frozenset({"printers"})

        def printers(self):
            return []

    t = Tiny()
    assert t.printers() == [] and not t.has("alarms")
    with pytest.raises(FarmUnsupported) as e:
        t.alarms()
    assert isinstance(e.value, ApiError) and e.value.capability == "alarms"


def test_the_farm_is_the_farm(monkeypatch):
    from app import clients
    sentinel = object()
    monkeypatch.setattr(clients, "openprinthq", lambda: sentinel)
    assert clients.farm() is sentinel and clients.FARM_KINDS == ("openprinthq",)
