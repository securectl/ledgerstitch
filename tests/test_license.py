# SPDX-License-Identifier: AGPL-3.0-only
import base64
import json
import os
import stat
from datetime import date
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ledgerstitch.security import license as L


@pytest.fixture
def keypair():
    sk = Ed25519PrivateKey.generate()
    pem = sk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption())
    pub = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return pem, pub


def test_issue_verify_roundtrip(keypair):
    pem, pub = keypair
    k = L.issue(pem, "sam@example.com", "pro", date(2030, 1, 1), issued=date(2026, 1, 1))
    assert k.startswith("LS1.") and k.count(".") == 2
    lic = L.verify(k, public_key=pub)
    assert lic.valid and lic.plan == "pro" and lic.email == "sam@example.com"
    assert lic.expires == date(2030, 1, 1) and lic.issued == date(2026, 1, 1)


def test_payload_format(keypair):
    pem, _ = keypair
    k = L.issue(pem, "a@b.co", "enterprise", None)
    seg = k.split(".")[1]
    p = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
    assert set(p) == {"email", "plan", "expires", "issued"} and p["expires"] is None


def test_never_expires(keypair):
    pem, pub = keypair
    lic = L.verify(L.issue(pem, "a@b.co", "enterprise", None), public_key=pub, today=date(2099, 1, 1))
    assert lic.valid and lic.expires is None


def test_expired(keypair):
    pem, pub = keypair
    k = L.issue(pem, "a@b.co", "pro", date(2026, 1, 1))
    assert L.verify(k, public_key=pub, today=date(2026, 1, 1)).valid  # inclusive
    lic = L.verify(k, public_key=pub, today=date(2026, 1, 2))
    assert not lic.valid and lic.reason == "expired"


def test_wrong_signer_rejected(keypair):
    pem, _ = keypair
    other = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    lic = L.verify(L.issue(pem, "a@b.co", "pro", None), public_key=other)
    assert not lic.valid and lic.reason == "bad signature"


def test_tampered_payload_rejected(keypair):
    pem, pub = keypair
    pre, seg, sig = L.issue(pem, "a@b.co", "pro", None).split(".")
    p = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
    p["plan"] = "enterprise"
    seg2 = base64.urlsafe_b64encode(json.dumps(p).encode()).rstrip(b"=").decode()
    assert not L.verify(f"{pre}.{seg2}.{sig}", public_key=pub).valid


@pytest.mark.parametrize("bad", ["", "garbage", "LS1.a.b", "LL2.x.y", "LS1..", "LS1.!!!.###",
                                 "LS1." + "A" * 5000 + ".x", None, 123])
def test_malformed_never_raises(bad):
    lic = L.verify(bad)
    assert not lic.valid and lic.plan == "free"


def test_whitespace_tolerated(keypair):
    pem, pub = keypair
    k = L.issue(pem, "a@b.co", "pro", None)
    assert L.verify("  " + k[:20] + "\n" + k[20:] + " \n", public_key=pub).valid


def test_current_plan_free_fallback():
    for k in (None, "", "LS1.nope.nope"):
        lic = L.current_plan(k)
        assert lic.plan == "free" and not lic.valid
        assert lic.limits == L.limits_for("free")


def test_current_plan_with_embedded_key(monkeypatch, keypair):
    pem, pub = keypair
    monkeypatch.setattr(L, "PUBLIC_KEY_B64", base64.b64encode(pub).decode())
    lic = L.current_plan(L.issue(pem, "b@c.io", "pro", None))
    assert lic.valid and lic.plan == "pro"


def test_limits_are_uncapped():
    assert not hasattr(L, "FREE_LIMITS")
    for plan in ("free", *L.PLANS, "bogus"):
        assert L.limits_for(plan) == {"accounts": None, "transactions": None, "vaults": None}


def test_core_plans_and_plans_declared_by_add_ons(keypair, fresh_registry):
    pem, pub = keypair
    assert L.PLANS == ("pro", "enterprise")
    for plan in L.PLANS:
        lic = L.verify(L.issue(pem, "a@b.co", plan, None), public_key=pub)
        assert lic.valid and lic.plan == plan
    assert not L.known_plan("team_x")
    from ledgerstitch import plugins

    def reg(r):
        r.license_plans({"team_x": ["pro", "team"]}, labels={"team_x": "Team X"}, aliases={"old_x": "team_x"})
    plugins.register_local(reg, "planner")
    assert L.known_plan("team_x")
    lic = L.verify(L.issue(pem, "a@b.co", "old_x", None), public_key=pub)
    assert lic.valid and lic.plan == "team_x"          # an alias is signed and reported as its plan


def test_issue_validation(keypair):
    pem, _ = keypair
    with pytest.raises(L.LicenseError):
        L.issue(pem, "a@b.co", "platinum", None)
    with pytest.raises(L.LicenseError):
        L.issue(pem, "not-an-email", "pro", None)
    with pytest.raises(L.LicenseError):
        L.issue(b"not a pem", "a@b.co", "pro", None)


def test_unknown_plan_in_signed_payload_invalid(keypair):
    pem, pub = keypair
    sk = serialization.load_pem_private_key(pem, None)
    seg = base64.urlsafe_b64encode(json.dumps(
        {"email": "a@b.co", "plan": "god", "expires": None, "issued": "2026-01-01"}).encode()).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(sk.sign(f"LS1.{seg}".encode())).rstrip(b"=").decode()
    lic = L.verify(f"LS1.{seg}.{sig}", public_key=pub)
    assert not lic.valid and lic.reason == "unknown plan"


def test_embedded_key_matches_dev_signing_key():
    """If the seller's key is present on this machine, it must match license.py."""
    p = Path(__file__).resolve().parents[2] / "ledgerstitch_signing_key.pem"
    if not p.exists():
        pytest.skip("seller signing key not present")
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    k = L.issue(p.read_bytes(), "dev@example.com", "pro", None)
    assert L.verify(k).valid


def test_no_private_key_in_module():
    src = Path(L.__file__).read_text()
    assert "PRIVATE KEY" not in src


# ---------------------------------------------------------------- developer override (source runs only)
def test_dev_override_unlocks_in_source_runs(monkeypatch):
    monkeypatch.setattr(L.sys, "frozen", False, raising=False)
    monkeypatch.setenv("LEDGERSTITCH_DEV_PLAN", "enterprise")
    lic = L.current_plan(None)
    assert lic.valid and lic.plan == "enterprise" and lic.email == "developer@local"
    assert lic.reason == "developer override" and lic.expires is None


def test_dev_override_ignored_when_frozen(monkeypatch):
    monkeypatch.setenv("LEDGERSTITCH_DEV_PLAN", "enterprise")
    monkeypatch.setattr(L.sys, "frozen", True, raising=False)
    lic = L.current_plan(None)
    assert not lic.valid and lic.plan == "free" and lic.reason != "developer override"


@pytest.mark.parametrize("value", ["", "free", "god", "PRO ; rm -rf", "nosuchplan"])
def test_dev_override_ignored_for_invalid_plan(monkeypatch, value):
    monkeypatch.setattr(L.sys, "frozen", False, raising=False)
    monkeypatch.setenv("LEDGERSTITCH_DEV_PLAN", value)
    lic = L.current_plan(None)
    assert not lic.valid and lic.plan == "free"


def test_dev_override_env_only_and_status_flag(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from ledgerstitch.api import create_app
    monkeypatch.delenv("LEDGERSTITCH_DEV_PLAN", raising=False)
    tok = "t" * 43
    app = create_app(tmp_path, tok, port=8765)
    with TestClient(app, base_url="http://127.0.0.1:8765", headers={"X-LedgerStitch-Token": tok}) as c:
        assert c.get("/api/status").json()["dev_override"] is False
        c.post("/api/vaults", json={"name": "Acme", "passphrase": "correct horse battery"})
        # a vault setting can never enable it
        c.app.state.session.ws.settings["LEDGERSTITCH_DEV_PLAN"] = "pro"
        st = c.get("/api/status").json()
        assert st["dev_override"] is False and st["plan"] == "free"
        monkeypatch.setenv("LEDGERSTITCH_DEV_PLAN", "pro")
        st = c.get("/api/status").json()
        assert st["dev_override"] is True and st["plan"] == "pro" and st["license"]["valid"] is True
