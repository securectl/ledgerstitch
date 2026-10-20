# SPDX-License-Identifier: AGPL-3.0-only
"""Recovery key: a second wrap of the DEK under a key derived from a random code."""
import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

from backend_fakes import install_missing
from ledgerstitch.api import create_app
from ledgerstitch.security import vault as vault_mod
from ledgerstitch.security.vault import Vault, VaultError, WrongPassphrase, WrongRecoveryCode

PW = "correct horse battery staple"
NEW = "a brand new passphrase"
CODE_RE = re.compile(r"^[A-Z2-7]{4}(?:-[A-Z2-7]{4}){7}$")


@pytest.fixture
def vault(tmp_path):
    v = Vault.create(tmp_path / "v", PW, "Acme Plumbing LLC")
    v.put("transactions", b"synthetic bytes")
    yield v
    v.lock()


def _header(path):
    return json.loads((path / "vault.json").read_text("utf-8"))


def _write_header(path, h):
    (path / "vault.json").write_text(json.dumps(h), "utf-8")


def test_code_format_and_entropy(vault):
    code = vault.set_recovery_code(PW)
    assert CODE_RE.match(code)
    assert vault_mod.RECOVERY_BYTES * 8 >= 128
    assert vault.has_recovery and vault.recovery_created
    assert code not in (vault.path / "vault.json").read_text("utf-8")
    assert code not in repr(vault)


def test_recovery_unlock_sets_new_passphrase(tmp_path, vault):
    code = vault.set_recovery_code(PW)
    vault.lock()
    v = Vault.recover(tmp_path / "v", code, NEW)
    assert v.name == "Acme Plumbing LLC" and v.get("transactions") == b"synthetic bytes"
    v.lock()
    with pytest.raises(WrongPassphrase):           # the forgotten passphrase is retired
        Vault.open(tmp_path / "v", PW)
    assert Vault.open(tmp_path / "v", NEW).get("transactions") == b"synthetic bytes"
    # the recovery key keeps working after use
    Vault.recover(tmp_path / "v", code, "yet another passphrase").lock()


def test_code_is_forgiving_about_case_dashes_and_lookalikes(tmp_path, vault):
    code = vault.set_recovery_code(PW)
    vault.lock()
    sloppy = code.replace("-", " ").lower().replace("o", "0").replace("i", "1").replace("b", "8")
    Vault.recover(tmp_path / "v", sloppy, NEW).lock()


def test_wrong_code_fails(tmp_path, vault):
    code = vault.set_recovery_code(PW)
    vault.lock()
    wrong = ("B" if code[0] != "B" else "C") + code[1:]
    with pytest.raises(WrongRecoveryCode):
        Vault.recover(tmp_path / "v", wrong, NEW)
    with pytest.raises(VaultError):                # malformed: not even tried
        Vault.recover(tmp_path / "v", "ABCD-EFGH", NEW)
    assert Vault.open(tmp_path / "v", PW).name     # nothing changed


def test_regenerate_invalidates_old_code(tmp_path, vault):
    old = vault.set_recovery_code(PW)
    new = vault.set_recovery_code(PW)
    assert old != new
    vault.lock()
    with pytest.raises(WrongRecoveryCode):
        Vault.recover(tmp_path / "v", old, NEW)
    Vault.recover(tmp_path / "v", new, NEW).lock()


def test_remove_recovery(tmp_path, vault):
    code = vault.set_recovery_code(PW)
    with pytest.raises(WrongPassphrase):
        vault.remove_recovery_code("not it")
    vault.remove_recovery_code(PW)
    assert not vault.has_recovery and "recovery" not in _header(tmp_path / "v")
    assert not Vault.has_recovery_key(tmp_path / "v")
    vault.lock()
    with pytest.raises(WrongRecoveryCode):
        Vault.recover(tmp_path / "v", code, NEW)


def test_set_recovery_requires_current_passphrase(vault):
    with pytest.raises(WrongPassphrase):
        vault.set_recovery_code("not it")
    assert not vault.has_recovery


@pytest.mark.parametrize("mutate", [
    lambda r: r["kdf"].__setitem__("time_cost", r["kdf"]["time_cost"] + 1),
    lambda r: r["kdf"].__setitem__("salt", "AAAAAAAAAAAAAAAAAAAAAA=="),
    lambda r: r["kdf"].__setitem__("memory_cost", 32 * 1024),
])
def test_recovery_header_tamper_fails(tmp_path, vault, mutate):
    code = vault.set_recovery_code(PW)
    vault.lock()
    h = _header(tmp_path / "v")
    mutate(h["recovery"])
    _write_header(tmp_path / "v", h)
    with pytest.raises(WrongRecoveryCode):
        Vault.recover(tmp_path / "v", code, NEW)


def test_recovery_header_out_of_range_rejected(tmp_path, vault):
    code = vault.set_recovery_code(PW)
    vault.lock()
    h = _header(tmp_path / "v")
    h["recovery"]["kdf"]["memory_cost"] = 8           # trivially weak
    _write_header(tmp_path / "v", h)
    with pytest.raises(VaultError, match="out of range"):
        Vault.recover(tmp_path / "v", code, NEW)
    h["recovery"] = "garbage"
    _write_header(tmp_path / "v", h)
    with pytest.raises(VaultError):
        Vault.open(tmp_path / "v", PW)


def test_recovery_wrap_cannot_stand_in_for_passphrase_wrap(tmp_path, vault):
    """Swapping the recovery wrap into the passphrase slot must not open anything."""
    vault.set_recovery_code(PW)
    vault.lock()
    h = _header(tmp_path / "v")
    h["kdf"], h["wrapped_dek"] = h["recovery"]["kdf"], h["recovery"]["wrapped_dek"]
    _write_header(tmp_path / "v", h)
    with pytest.raises(WrongPassphrase):
        Vault.open(tmp_path / "v", PW)


def test_legacy_vault_without_recovery_still_opens(tmp_path, vault):
    vault.lock()
    h = _header(tmp_path / "v")
    assert "recovery" not in h and h["version"] == vault_mod.VERSION == 1
    v = Vault.open(tmp_path / "v", PW)
    assert not v.has_recovery and v.recovery_created is None
    with pytest.raises(WrongRecoveryCode):
        Vault.recover(tmp_path / "v", "A" * 32, NEW)


def test_change_passphrase_keeps_recovery_working(tmp_path, vault):
    code = vault.set_recovery_code(PW)
    rec_before = _header(tmp_path / "v")["recovery"]
    vault.change_passphrase(PW, NEW)
    assert _header(tmp_path / "v")["recovery"] == rec_before
    vault.lock()
    Vault.open(tmp_path / "v", NEW).lock()
    Vault.recover(tmp_path / "v", code, "third passphrase").lock()


# ---------------------------------------------------------------- API
TOKEN = "t" * 43
BASE = "http://127.0.0.1:8765"
API_PW = "correct horse battery"


@pytest.fixture
def client(tmp_path, monkeypatch):
    install_missing(monkeypatch)
    app = create_app(tmp_path, TOKEN, port=8765)
    with TestClient(app, base_url=BASE, headers={"X-LedgerStitch-Token": TOKEN}) as c:
        yield c


def test_api_create_with_recovery_and_recover(client, caplog):
    caplog.set_level(logging.DEBUG)
    r = client.post("/api/vaults", json={"name": "Acme Studio", "passphrase": API_PW, "recovery": True})
    assert r.status_code == 201, r.text
    code = r.json()["recovery_code"]
    assert CODE_RE.match(code) and r.json()["recovery"]["enabled"]
    path = r.json()["vault_path"]
    assert client.get("/api/status").json()["recovery"]["enabled"]
    assert [v["recovery"] for v in client.get("/api/vaults").json()] == [True]
    client.post("/api/lock")

    r = client.post("/api/unlock/recovery", json={"path": path, "recovery_code": "A" * 32,
                                                  "new_passphrase": "new passphrase 123"})
    assert r.status_code == 401
    r = client.post("/api/unlock/recovery", json={"path": path, "recovery_code": "nope",
                                                  "new_passphrase": "new passphrase 123"})
    assert r.status_code == 422 and "nope" not in r.text
    r = client.post("/api/unlock/recovery", json={"path": path, "recovery_code": code, "new_passphrase": "short"})
    assert r.status_code == 422
    r = client.post("/api/unlock/recovery", json={"path": path, "recovery_code": code,
                                                  "new_passphrase": "new passphrase 123"})
    assert r.status_code == 200 and r.json()["unlocked"]
    client.post("/api/lock")
    assert client.post("/api/unlock", json={"path": path, "passphrase": API_PW}).status_code == 401
    assert client.post("/api/unlock", json={"path": path, "passphrase": "new passphrase 123"}).status_code == 200
    assert code not in caplog.text


def test_api_default_create_has_no_recovery(client):
    r = client.post("/api/vaults", json={"name": "Acme Studio", "passphrase": API_PW})
    assert r.status_code == 201 and "recovery_code" not in r.json()
    assert r.json()["recovery"] == {"supported": True, "enabled": False, "created": None}
    path = r.json()["vault_path"]
    client.post("/api/lock")
    r = client.post("/api/unlock/recovery", json={"path": path, "recovery_code": "A" * 32,
                                                  "new_passphrase": "new passphrase 123"})
    assert r.status_code == 422


def test_api_regenerate_and_remove(client):
    path = client.post("/api/vaults", json={"name": "Acme Studio", "passphrase": API_PW}).json()["vault_path"]
    assert client.post("/api/recovery", json={"passphrase": "wrong pass"}).status_code == 401
    first = client.post("/api/recovery", json={"passphrase": API_PW}).json()["recovery_code"]
    r = client.post("/api/recovery", json={"passphrase": API_PW})
    second = r.json()["recovery_code"]
    assert first != second and r.json()["enabled"]
    assert client.post("/api/recovery/remove", json={"passphrase": "wrong pass"}).status_code == 401
    r = client.post("/api/recovery/remove", json={"passphrase": API_PW})
    assert r.status_code == 200 and r.json()["enabled"] is False
    client.post("/api/lock")
    r = client.post("/api/unlock/recovery", json={"path": path, "recovery_code": second,
                                                  "new_passphrase": "new passphrase 123"})
    assert r.status_code == 422


def test_api_recovery_routes_need_unlock_and_token(client, tmp_path):
    assert client.post("/api/recovery", json={"passphrase": API_PW}).status_code == 409
    app = create_app(tmp_path / "other", TOKEN, port=8765)
    with TestClient(app, base_url=BASE) as anon:
        assert anon.post("/api/unlock/recovery", json={"path": "x", "recovery_code": "A" * 32,
                                                       "new_passphrase": "new passphrase"}).status_code == 401
