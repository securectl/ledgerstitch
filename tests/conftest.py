# SPDX-License-Identifier: AGPL-3.0-only
"""Test-wide guards."""
import os

import pytest

from ledgerstitch import editions, plugins, store

if os.environ.get("LEDGERSTITCH_EDITION"):
    editions.configure(env=os.environ)      # e.g. the community-only CI job


@pytest.fixture(autouse=True)
def _no_dev_license_override(monkeypatch):
    """A developer's LEDGERSTITCH_DEV_PLAN must not leak into tests (tests opt in explicitly)."""
    monkeypatch.delenv("LEDGERSTITCH_DEV_PLAN", raising=False)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Tests never reach the network: add-ons that fetch data honour LEDGERSTITCH_OFFLINE=1."""
    monkeypatch.setenv("LEDGERSTITCH_OFFLINE", "1")


@pytest.fixture
def community(monkeypatch):
    """Pure Community edition for one test: no add-ons, even if one is installed."""
    monkeypatch.setattr(plugins, "_registry", plugins.Registry())
    monkeypatch.setattr(plugins, "_loaded", True)
    monkeypatch.setattr(editions, "_override", "community")
    return plugins.registry()


@pytest.fixture
def pro_plan(monkeypatch):
    """A valid license on the "pro" plan for every workspace."""
    from ledgerstitch.security.license import License
    lic = License("a@b.co", "pro", None, True)
    monkeypatch.setattr(store, "_current_plan", lambda key: lic)
    return lic


@pytest.fixture
def plan(monkeypatch):
    """``plan("<id>")``: a valid license on that plan for every workspace."""
    from ledgerstitch.security.license import License

    def use(plan_id: str):
        lic = License("a@b.co", plan_id, None, True)
        monkeypatch.setattr(store, "_current_plan", lambda key: lic)
        return lic
    return use


@pytest.fixture
def fresh_registry(monkeypatch):
    """An empty registry the test fills with plugins.register_local() (edition auto-detected)."""
    monkeypatch.delenv("LEDGERSTITCH_EDITION", raising=False)
    monkeypatch.setattr(plugins, "_registry", plugins.Registry())
    monkeypatch.setattr(plugins, "_loaded", True)
    monkeypatch.setattr(editions, "_override", None)
    return plugins.registry()
