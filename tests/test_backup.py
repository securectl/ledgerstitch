# SPDX-License-Identifier: AGPL-3.0-only
"""Encrypted backups (.lsbackup) and hostile-archive-safe restore."""
import io
import json
import stat
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend_fakes import SAMPLE_CSV, install_missing
from ledgerstitch import store
from ledgerstitch.api import create_app
from ledgerstitch.security import backup as bk
from ledgerstitch.security.backup import BackupError, create_backup, read_manifest, restore_backup
from ledgerstitch.security.vault import Vault, WrongPassphrase

PW = "correct horse battery staple"


@pytest.fixture
def vault(tmp_path):
    v = Vault.create(tmp_path / "src", PW, "Acme Plumbing LLC")
    v.put("transactions", b"NETFLIX.COM 15.99 SYNTHETIC")
    v.put_json("settings", {"business_name": "Acme Plumbing LLC"})
    yield v
    v.lock()


def _backup_bytes(v) -> bytes:
    buf = io.BytesIO()
    create_backup(v, buf)
    return buf.getvalue()


def _zip(members: dict, *, symlink: str | None = None) -> bytes:
    """Build an archive by hand (to model a malicious one)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            zi = zipfile.ZipInfo(name)
            if name == symlink:
                zi.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(zi, data)
    return buf.getvalue()


def _members(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_backup_is_ciphertext_only(vault):
    data = _backup_bytes(vault)
    assert b"NETFLIX" not in data and b"Acme Plumbing" not in data and PW.encode() not in data
    m = _members(data)
    assert "manifest.json" in m and "vault/vault.json" in m
    assert all(n == "manifest.json" or bk._ENTRY_RE.match(n) for n in m)
    man = json.loads(m["manifest.json"])
    assert man["format"] == "ledgerstitch-backup" and set(man["files"]) == set(m) - {"manifest.json"}


def test_round_trip_restore_into_new_folder_and_unlock(tmp_path, vault):
    code = vault.set_recovery_code(PW)
    data = _backup_bytes(vault)
    dest = restore_backup(data, tmp_path / "restored")
    assert sorted(p.name for p in dest.iterdir()) == sorted(p.name for p in (tmp_path / "src").iterdir())
    assert stat.S_IMODE(dest.stat().st_mode) == 0o700
    v = Vault.open(dest, PW)
    assert v.name == "Acme Plumbing LLC" and v.get("transactions") == b"NETFLIX.COM 15.99 SYNTHETIC"
    v.lock()
    with pytest.raises(WrongPassphrase):
        Vault.open(dest, "not it")
    Vault.recover(dest, code, "new passphrase here").lock()      # recovery code works on a restore


def test_read_manifest_validates_without_extracting(tmp_path, vault):
    man = read_manifest(_backup_bytes(vault))
    assert man["header"]["format"] == "ledgerstitch-vault"
    assert not (tmp_path / "restored").exists()


def test_restore_never_overwrites(tmp_path, vault):
    data = _backup_bytes(vault)
    with pytest.raises(BackupError, match="already exists"):
        restore_backup(data, tmp_path / "src")                    # the live vault
    (tmp_path / "empty").mkdir()
    with pytest.raises(BackupError, match="already exists"):
        restore_backup(data, tmp_path / "empty")                  # even an empty folder
    assert Vault.open(tmp_path / "src", PW).get("transactions") == b"NETFLIX.COM 15.99 SYNTHETIC"


@pytest.mark.parametrize("bad_name", [
    "vault/../../evil.bin", "../evil.bin", "/etc/passwd", "vault/sub/aaaaaaaaaaaaaaaaaaaaaaaa.bin",
    "vault\\..\\evil.bin", "vault/notes.txt", "vault/", "evil.json",
])
def test_traversal_and_unexpected_names_rejected(tmp_path, vault, bad_name):
    m = _members(_backup_bytes(vault))
    m[bad_name] = b"x"
    with pytest.raises(BackupError, match="unexpected file name|folder entry"):
        restore_backup(_zip(m), tmp_path / "restored")
    assert not (tmp_path / "restored").exists() and not (tmp_path / "evil.bin").exists()


def test_symlink_member_rejected(tmp_path, vault):
    m = _members(_backup_bytes(vault))
    victim = sorted(n for n in m if n.endswith(".bin"))[0]
    m[victim] = b"/etc/passwd"
    with pytest.raises(BackupError, match="link"):
        restore_backup(_zip(m, symlink=victim), tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_oversized_member_rejected(tmp_path, vault, monkeypatch):
    m = _members(_backup_bytes(vault))
    monkeypatch.setattr(bk, "MAX_MEMBER_BYTES", 64)
    with pytest.raises(BackupError, match="too large"):
        restore_backup(_zip(m), tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_lying_size_header_caught_while_streaming(tmp_path, vault, monkeypatch):
    """A member whose zip header under-reports its size is still stopped mid-stream."""
    m = _members(_backup_bytes(vault))
    name = sorted(n for n in m if n.endswith(".bin"))[0]
    real = zipfile.ZipFile(io.BytesIO(_zip(m))).getinfo(name).file_size
    monkeypatch.setattr(bk, "MAX_MEMBER_BYTES", real - 1)
    monkeypatch.setattr(bk, "_check_members", lambda z: {zi.filename: zi for zi in z.infolist()})
    with pytest.raises(BackupError):
        restore_backup(_zip(m), tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_total_size_cap(tmp_path, vault, monkeypatch):
    monkeypatch.setattr(bk, "MAX_TOTAL_BYTES", 100)
    with pytest.raises(BackupError, match="too large"):
        restore_backup(_backup_bytes(vault), tmp_path / "restored")


def test_too_many_members(tmp_path, vault, monkeypatch):
    monkeypatch.setattr(bk, "MAX_MEMBERS", 1)
    with pytest.raises(BackupError, match="too many"):
        restore_backup(_backup_bytes(vault), tmp_path / "restored")


@pytest.mark.parametrize("case,why", [
    ("not-zip", "not a LedgerStitch backup"), ("no-manifest", "missing"), ("bad-format", "unsupported backup"),
    ("bad-header", "header is not valid"), ("weak-kdf", "header is not valid"),
    ("manifest-mismatch", "does not match its contents"), ("checksum", "damaged"), ("duplicate", "duplicate"),
])
def test_malformed_backups_rejected(tmp_path, vault, case, why):
    m = _members(_backup_bytes(vault))
    man = json.loads(m["manifest.json"])
    hdr = json.loads(m["vault/vault.json"])
    if case == "not-zip":
        data = b"PK\x03\x04 definitely not a zip"
    else:
        if case == "no-manifest":
            del m["manifest.json"]
        elif case == "bad-format":
            man["format"] = "something-else"
        elif case == "bad-header":
            hdr["format"] = "nope"
        elif case == "weak-kdf":
            hdr["kdf"]["memory_cost"] = 8
        elif case == "manifest-mismatch":
            man["files"]["vault/" + "0" * 24 + ".bin"] = {"size": 1, "sha256": "0" * 64}
        elif case == "checksum":
            n = sorted(x for x in m if x.endswith(".bin"))[0]
            m[n] = bytes([m[n][0] ^ 1]) + m[n][1:]
        if case not in ("no-manifest",):
            m["manifest.json"] = json.dumps(man).encode()
        if case in ("bad-header", "weak-kdf"):
            m["vault/vault.json"] = json.dumps(hdr).encode()
            man["files"]["vault/vault.json"]["size"] = len(m["vault/vault.json"])
            m["manifest.json"] = json.dumps(man).encode()
        data = _zip(m)
        if case == "duplicate":
            buf = io.BytesIO(data)
            with pytest.warns(UserWarning), zipfile.ZipFile(buf, "a") as z:
                z.writestr("vault/vault.json", m["vault/vault.json"])
            data = buf.getvalue()
    with pytest.raises(BackupError, match=why):
        restore_backup(data, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_backup_skips_stray_files(tmp_path, vault):
    (tmp_path / "src" / "notes.txt").write_text("not ours")
    (tmp_path / "src" / ".tmp-x.part").write_bytes(b"partial")
    names = set(_members(_backup_bytes(vault)))
    assert "vault/notes.txt" not in names and not any(".part" in n for n in names)


# ---------------------------------------------------------------- backup reminder
def test_backup_status_reminder(tmp_path):
    vault = Vault.create(tmp_path / "ws", PW, "Acme Studio")
    ws = store.Workspace(vault)
    assert ws.backup_status()["due"] is False                     # no data yet: no nagging
    ws.accounts["a"] = store.account_from_dict({"id": "a", "label": "Checking", "institution": "Test",
                                                 "kind": "checking", "last4": None})
    st = ws.backup_status()
    assert st["due"] is True and st["last_backup_at"] is None
    now = datetime.now(timezone.utc)
    ws.mark_backup(now - timedelta(days=10))
    assert ws.backup_status(now)["due"] is False and ws.backup_status(now)["days_since"] == 10
    ws.mark_backup(now - timedelta(days=31))
    assert ws.backup_status(now)["due"] is True
    assert store.Workspace(vault).settings["last_backup_at"]      # persisted (encrypted)


# ---------------------------------------------------------------- API
TOKEN = "t" * 43
BASE = "http://127.0.0.1:8765"
API_PW = "correct horse battery"


@pytest.fixture
def client(tmp_path, monkeypatch):
    install_missing(monkeypatch)
    app = create_app(tmp_path / "data", TOKEN, port=8765)
    with TestClient(app, base_url=BASE, headers={"X-LedgerStitch-Token": TOKEN}) as c:
        yield c


def test_api_backup_restore_round_trip(client, tmp_path):
    r = client.post("/api/vaults", json={"name": "Acme Studio", "passphrase": API_PW, "recovery": True})
    code = r.json()["recovery_code"]
    src_path = r.json()["vault_path"]
    up = client.post("/api/imports", files=[("file", ("bank.csv", SAMPLE_CSV, "text/csv"))],
                     data={"account_label": "Biz Checking"})
    assert up.status_code == 200, up.text
    st = client.get("/api/status").json()
    assert st["backup"]["due"] is True and st["backup"]["last_backup_at"] is None

    r = client.get("/api/backup")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/octet-stream"
    assert ".lsbackup" in r.headers["content-disposition"]
    data = r.content
    assert b"Acme Studio" not in data
    st = client.get("/api/status").json()
    assert st["backup"]["due"] is False and st["backup"]["last_backup_at"]
    n_txns = st["counts"]["transactions"]
    client.post("/api/lock")

    r = client.post("/api/vaults/restore", files=[("file", ("acme.lsbackup", data, "application/octet-stream"))])
    assert r.status_code == 201, r.text
    restored = r.json()["restored"]
    assert restored["path"] != src_path and restored["recovery"] is True
    assert len(client.get("/api/vaults").json()) == 2
    r = client.post("/api/unlock/recovery", json={"path": restored["path"], "recovery_code": code,
                                                  "new_passphrase": "restored passphrase"})
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["transactions"] == n_txns and r.json()["vault_name"] == "Acme Studio"
    client.post("/api/lock")
    assert client.post("/api/unlock", json={"path": src_path, "passphrase": API_PW}).status_code == 200


def test_api_restore_rejects_bad_archives(client):
    for data in (b"not a zip", _zip({"../evil": b"x", "manifest.json": b"{}"})):
        r = client.post("/api/vaults/restore", files=[("file", ("x.lsbackup", data, "application/octet-stream"))])
        assert r.status_code == 422 and "can't be restored" in r.json()["error"]
    assert client.get("/api/vaults").json() == []
    assert client.post("/api/vaults/restore", files=[]).status_code == 422


def test_api_backup_needs_unlock(client):
    assert client.get("/api/backup").status_code == 409
