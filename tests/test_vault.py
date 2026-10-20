# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
import stat
from decimal import Decimal
from datetime import date

import pytest

from ledgerstitch.security.vault import Vault, VaultError, WrongPassphrase, _file_for

PW = "correct horse battery staple"


@pytest.fixture
def vault(tmp_path):
    v = Vault.create(tmp_path / "v", PW, "Acme Plumbing LLC")
    yield v
    v.lock()


def test_create_and_reopen_roundtrip(tmp_path, vault):
    vault.put("transactions", b"secret bytes")
    vault.put_json("settings", {"a": 1, "amt": Decimal("12.50"), "d": date(2025, 1, 2)})
    vault.lock()
    v2 = Vault.open(tmp_path / "v", PW)
    assert v2.name == "Acme Plumbing LLC"
    assert v2.get("transactions") == b"secret bytes"
    assert v2.get_json("settings") == {"a": 1, "amt": "12.50", "d": "2025-01-02"}
    assert v2.keys() == ["settings", "transactions"]


def test_exists(tmp_path, vault):
    assert Vault.exists(tmp_path / "v")
    assert not Vault.exists(tmp_path / "nope")


def test_wrong_passphrase(tmp_path, vault):
    with pytest.raises(WrongPassphrase):
        Vault.open(tmp_path / "v", "not it")
    assert issubclass(WrongPassphrase, VaultError)


def test_open_missing_raises(tmp_path):
    with pytest.raises(VaultError):
        Vault.open(tmp_path / "missing", PW)


def test_create_twice_refused(tmp_path, vault):
    with pytest.raises(VaultError):
        Vault.create(tmp_path / "v", PW, "x")


def test_get_missing_and_default(vault):
    assert vault.get("nope") is None
    assert vault.get_json("nope", default=[]) == []


def test_delete(vault):
    vault.put("k", b"1")
    vault.delete("k")
    assert vault.get("k") is None
    assert "k" not in vault.keys()
    vault.delete("k")  # idempotent


def test_overwrite(vault):
    vault.put("k", b"1")
    vault.put("k", b"2")
    assert vault.get("k") == b"2"
    assert vault.keys() == ["k"]


def test_no_plaintext_on_disk(tmp_path):
    d = tmp_path / "v"
    v = Vault.create(d, PW, "Secret Client Name", label="Client 7")
    v.put("transactions", b"NETFLIX.COM 15.99 JOHN SMITH")
    v.put_json("accounts", [{"label": "Chase 4012"}])
    blob = b"".join(p.read_bytes() for p in d.iterdir())
    for needle in (b"NETFLIX", b"JOHN SMITH", b"Chase 4012", b"transactions", b"accounts",
                   b"Secret Client Name"):
        assert needle not in blob
    hdr = json.loads((d / "vault.json").read_text())
    assert hdr["label"] == "Client 7"
    assert Vault.read_label(d) == "Client 7"
    assert v.name == "Secret Client Name"


def test_label_defaults_to_empty_and_name_stays_encrypted(tmp_path):
    v = Vault.create(tmp_path / "v", PW, "Sam's Shop")
    assert Vault.read_label(tmp_path / "v") == ""
    blob = b"".join(p.read_bytes() for p in (tmp_path / "v").iterdir())
    assert b"Sam" not in blob
    v.lock()
    assert Vault.open(tmp_path / "v", PW).name == "Sam's Shop"


def test_file_layout_and_permissions(tmp_path, vault):
    vault.put("transactions", b"x")
    d = tmp_path / "v"
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700
    f = d / _file_for("transactions")
    assert f.exists() and f.name.endswith(".bin") and len(f.stem) == 24
    for p in d.iterdir():
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    raw = f.read_bytes()
    assert len(raw) == 12 + 1 + 16  # nonce || ct || tag
    assert not any(p.name.endswith(".part") for p in d.iterdir())


def test_aad_binds_key_name(tmp_path, vault):
    vault.put("a", b"AAA")
    vault.put("b", b"BBB")
    d = tmp_path / "v"
    # swap file contents: decrypting under the other key name must fail
    (d / _file_for("b")).write_bytes((d / _file_for("a")).read_bytes())
    with pytest.raises(VaultError):
        vault.get("b")


def test_tamper_detected(tmp_path, vault):
    vault.put("a", b"hello")
    f = tmp_path / "v" / _file_for("a")
    raw = bytearray(f.read_bytes())
    raw[-1] ^= 1
    f.write_bytes(bytes(raw))
    with pytest.raises(VaultError):
        vault.get("a")


def test_header_kdf_tamper_fails(tmp_path, vault):
    vault.lock()
    hp = tmp_path / "v" / "vault.json"
    h = json.loads(hp.read_text())
    h["kdf"]["time_cost"] = 4
    hp.write_text(json.dumps(h))
    with pytest.raises(WrongPassphrase):
        Vault.open(tmp_path / "v", PW)


def test_header_weak_params_rejected(tmp_path, vault):
    vault.lock()
    hp = tmp_path / "v" / "vault.json"
    h = json.loads(hp.read_text())
    h["kdf"]["memory_cost"] = 8
    hp.write_text(json.dumps(h))
    with pytest.raises(VaultError):
        Vault.open(tmp_path / "v", PW)


def test_lock_blocks_everything(vault):
    vault.put("k", b"v")
    dek = vault._dek
    vault.lock()
    assert dek == bytearray(32)  # zeroed in place
    assert vault.locked
    for call in (lambda: vault.get("k"), lambda: vault.put("k", b"x"), lambda: vault.keys(),
                 lambda: vault.delete("k"), lambda: vault.name, lambda: vault.get_json("k"),
                 lambda: vault.change_passphrase(PW, "new")):
        with pytest.raises(VaultError):
            call()


def test_change_passphrase_rewraps_only(tmp_path, vault):
    vault.put("k", b"data")
    f = tmp_path / "v" / _file_for("k")
    before = f.read_bytes()
    vault.change_passphrase(PW, "brand new passphrase")
    assert f.read_bytes() == before            # data files untouched
    assert vault.get("k") == b"data"           # still usable
    vault.lock()
    with pytest.raises(WrongPassphrase):
        Vault.open(tmp_path / "v", PW)
    v2 = Vault.open(tmp_path / "v", "brand new passphrase")
    assert v2.get("k") == b"data" and v2.name == "Acme Plumbing LLC"


def test_change_passphrase_wrong_old(vault):
    with pytest.raises(WrongPassphrase):
        vault.change_passphrase("wrong", "new")
    assert vault.get("missing") is None  # still unlocked and usable


def test_unicode_passphrase_normalized(tmp_path):
    composed = "café-päss"
    decomposed = "café-päss"
    Vault.create(tmp_path / "u", composed, "u").lock()
    Vault.open(tmp_path / "u", decomposed).lock()


def test_bad_inputs(tmp_path, vault):
    with pytest.raises(VaultError):
        Vault.create(tmp_path / "e", "", "name")
    with pytest.raises(VaultError):
        vault.put("", b"x")
    with pytest.raises(TypeError):
        vault.put("k", "not bytes")


def test_large_and_binary_payload(vault):
    data = os.urandom(2_000_000)
    vault.put("blob", data)
    assert vault.get("blob") == data


def test_repr_has_no_secrets(vault):
    r = repr(vault)
    assert "Acme" not in r and "dek" not in r.lower()
