from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_proxy_module() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "provider-egress-proxy.py"
    spec = importlib.util.spec_from_file_location("offerpilot_provider_egress_proxy", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_provider_tunnel_keeps_long_generation_connections_alive(monkeypatch) -> None:
    module = _load_proxy_module()
    observed_timeouts: list[float | None] = []

    class FakeSelector:
        def register(self, *_args: object) -> None:
            return None

        def select(self, timeout: float | None = None) -> list[object]:
            observed_timeouts.append(timeout)
            return []

        def close(self) -> None:
            return None

    monkeypatch.setattr(module.selectors, "DefaultSelector", FakeSelector)

    module._tunnel(object(), object())

    assert observed_timeouts == [300]
