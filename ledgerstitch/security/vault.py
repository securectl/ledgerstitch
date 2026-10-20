# SPDX-License-Identifier: AGPL-3.0-only
"""Encrypted on-disk workspace ("vault"), one per business / client.

Crypto design (see docs/SECURITY.md for the customer-facing version):

* passphrase --Argon2id(t=3, m=64 MiB, p=4, salt=16 random bytes)--> 32-byte KEK
* a random 32-byte DEK encrypts everything; the DEK itself is stored only
  wrapped (AES-256-GCM under the KEK) in ``vault.json``.
* every value is stored in its own file ``sha256(key)[:24].bin`` holding
  ``nonce(12) || AES-256-GCM(DEK, data, aad=key-name-bytes)``.  Binding the key
  name as AAD means a file cannot be swapped for another key's file without
  detection.
* key names are not visible on disk: file names are hashes, and the list of
  names lives in an encrypted index entry.
* the vault's real name is stored encrypted; the header only carries an
  optional plaintext *label* used by the vault picker before unlock. It is
  empty by default (client names must not be readable on disk); pass
  ``label=`` to opt into a visible label. The API also names new vault folders
  with a random id (``ws-<8 hex>.vault``) instead of the business name.
* the wrapped DEK uses the KDF parameters + salt + version as AAD, so tampering
  with the header makes unlock fail instead of silently weakening it.
* optional **recovery key**: a random 160-bit code (shown once as grouped
  base32, ``XXXX-XXXX-...``) derives a second KEK via Argon2id with its own
  salt; ``header["recovery"]`` holds a second wrap of the same DEK, AAD-bound
  to the recovery KDF params + version like the main wrap. Anyone holding the
  code can open the vault, so it is opt-in and meant to be kept offline.
  Headers without ``recovery`` (older vaults) open exactly as before.

Python cannot guarantee that secrets are scrubbed from memory (immutable
``bytes`` copies exist inside the KDF and the AES backend). We keep our own
copies in ``bytearray`` and zero them on :meth:`Vault.lock`; this is a
best-effort defence, documented as such.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import tempfile
import threading
import unicodedata
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

__all__ = ["VaultError", "WrongPassphrase", "WrongRecoveryCode", "Vault"]

HEADER_FILE = "vault.json"
FORMAT = "ledgerstitch-vault"
VERSION = 1

# Argon2id parameters (OWASP / RFC 9106 "second recommended" class).
KDF_TIME_COST = 3
KDF_MEMORY_COST = 64 * 1024        # KiB  -> 64 MiB
KDF_PARALLELISM = 4
KDF_HASH_LEN = 32
KDF_SALT_LEN = 16

# Sanity bounds when *reading* a header, so a tampered header can neither make
# us derive with trivially weak parameters nor exhaust memory/CPU.
_MIN_TIME, _MAX_TIME = 1, 20
_MIN_MEM, _MAX_MEM = 19 * 1024, 2 * 1024 * 1024
_MIN_PAR, _MAX_PAR = 1, 16

NONCE_LEN = 12
DEK_LEN = 32
_INDEX_KEY = "\x00ledgerstitch:index"      # cannot collide with user keys (NUL rejected)
_NAME_AAD = b"ledgerstitch:vault-name:v1"
_DEK_AAD_PREFIX = b"ledgerstitch:dek:v1:"
_RECOVERY_AAD_PREFIX = b"ledgerstitch:dek-recovery:v1:"
RECOVERY_BYTES = 20                 # 160 bits -> 32 base32 characters
_RECOVERY_GROUP = 4
_B32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
# Characters people misread when copying a code by hand.
_B32_FIXUPS = str.maketrans({"0": "O", "1": "I", "8": "B"})


class VaultError(Exception):
    """Any vault failure (missing, corrupt, locked, tampered)."""


class WrongPassphrase(VaultError):
    """The passphrase did not unwrap the vault key."""


class WrongRecoveryCode(VaultError):
    """The recovery code did not unwrap the vault key (or the vault has none)."""


# --------------------------------------------------------------------------- helpers

def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    try:
        return base64.b64decode(s.encode("ascii"), validate=True)
    except Exception as e:  # noqa: BLE001
        raise VaultError("vault header is corrupt") from e


def _norm_passphrase(passphrase: str) -> bytes:
    if not isinstance(passphrase, str) or passphrase == "":
        raise VaultError("passphrase must be a non-empty string")
    # NFC so the same passphrase typed on different OSes derives the same key.
    return unicodedata.normalize("NFC", passphrase).encode("utf-8")


def _derive_kek(passphrase: str, salt: bytes, t: int, m: int, p: int) -> bytearray:
    return _derive_raw(_norm_passphrase(passphrase), salt, t, m, p)


def _derive_raw(secret: bytes, salt: bytes, t: int, m: int, p: int) -> bytearray:
    raw = hash_secret_raw(
        secret=secret, salt=salt, time_cost=t, memory_cost=m,
        parallelism=p, hash_len=KDF_HASH_LEN, type=Type.ID,
    )
    return bytearray(raw)


def _new_kdf(salt: bytes) -> dict:
    return {
        "alg": "argon2id", "v": 19, "time_cost": KDF_TIME_COST,
        "memory_cost": KDF_MEMORY_COST, "parallelism": KDF_PARALLELISM,
        "hash_len": KDF_HASH_LEN, "salt": _b64e(salt),
    }


def _check_kdf(kdf: dict) -> None:
    """Reject unknown KDFs and parameters that are trivially weak or exhaust memory."""
    if kdf["alg"] != "argon2id" or kdf["hash_len"] != KDF_HASH_LEN:
        raise VaultError("unsupported KDF")
    ok = (_MIN_TIME <= int(kdf["time_cost"]) <= _MAX_TIME
          and _MIN_MEM <= int(kdf["memory_cost"]) <= _MAX_MEM
          and _MIN_PAR <= int(kdf["parallelism"]) <= _MAX_PAR)
    if not ok:
        raise VaultError("vault KDF parameters out of range")
    kdf["salt"]  # noqa: B018 presence


def format_recovery_code(raw: bytes) -> str:
    """``XXXX-XXXX-...``: base32 (no padding) in groups of four."""
    b32 = base64.b32encode(raw).decode("ascii").rstrip("=")
    return "-".join(b32[i:i + _RECOVERY_GROUP] for i in range(0, len(b32), _RECOVERY_GROUP))


def _norm_recovery_code(code: str) -> bytes:
    """Accept any case, spaces or dashes and the usual 0/O, 1/I, 8/B slips."""
    if not isinstance(code, str):
        raise VaultError("recovery code must be a string")
    s = "".join(ch for ch in code.upper().translate(_B32_FIXUPS) if ch not in " -\t\r\n")
    n = -(-RECOVERY_BYTES * 8 // 5)
    if len(s) != n or any(ch not in _B32_ALPHABET for ch in s):
        raise VaultError(f"a recovery code has {n} letters and digits (2-7), in groups of four")
    return base64.b32decode(s + "=" * (-len(s) % 8))


def _zero(buf: bytearray | None) -> None:
    if buf is not None:
        for i in range(len(buf)):
            buf[i] = 0


def _file_for(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24] + ".bin"


def _check_key(key: str) -> None:
    if not isinstance(key, str) or not key or "\x00" in key:
        raise VaultError("vault key must be a non-empty string without NUL")


def _dek_aad(kdf: dict, version: int) -> bytes:
    canon = json.dumps({"v": version, "kdf": kdf}, sort_keys=True, separators=(",", ":"))
    return _DEK_AAD_PREFIX + canon.encode("ascii")


def _recovery_aad(kdf: dict, version: int) -> bytes:
    # Own prefix: a recovery wrap can never be passed off as the passphrase wrap.
    canon = json.dumps({"v": version, "kdf": kdf}, sort_keys=True, separators=(",", ":"))
    return _RECOVERY_AAD_PREFIX + canon.encode("ascii")


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via a 0600 temp file in the same directory, fsync, os.replace."""
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    try:  # make the rename durable
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def _json_default(o: Any):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    if isinstance(o, Path):
        return str(o)
    if is_dataclass(o) and not isinstance(o, type):
        return o.to_dict() if hasattr(o, "to_dict") else asdict(o)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


# --------------------------------------------------------------------------- Vault

class Vault:
    """A directory holding an encrypted workspace (one per business/client)."""

    def __init__(self, path: Path, header: dict, dek: bytearray, name: str) -> None:
        # Use Vault.create / Vault.open; this constructor trusts its inputs.
        self._path = Path(path)
        self._header = header
        self._dek: bytearray | None = dek
        self._aead: AESGCM | None = AESGCM(bytes(dek))
        self._name = name
        self._lock = threading.RLock()
        self._index: list[str] | None = None

    # ---------------------------------------------------------------- lifecycle
    @staticmethod
    def exists(path: Path) -> bool:
        return (Path(path) / HEADER_FILE).is_file()

    @staticmethod
    def read_label(path: Path) -> str | None:
        """Plaintext display label from the header (no passphrase needed).

        This is the only user-supplied text readable without the passphrase.
        Returns None if the path is not a vault or the header is unreadable.
        """
        try:
            h = json.loads((Path(path) / HEADER_FILE).read_text("utf-8"))
            lab = h.get("label")
            return lab if isinstance(lab, str) else None
        except (OSError, ValueError):
            return None

    @classmethod
    def create(cls, path: Path, passphrase: str, name: str, *, label: str | None = None) -> "Vault":
        """Create a new vault. ``label`` is the optional plaintext picker label
        (default: empty, so nothing identifying is readable before unlock)."""
        path = Path(path)
        if cls.exists(path):
            raise VaultError("a vault already exists at this location")
        if not isinstance(name, str) or not name.strip():
            raise VaultError("vault name must be a non-empty string")
        _norm_passphrase(passphrase)  # validate early
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)

        salt = secrets.token_bytes(KDF_SALT_LEN)
        kdf = _new_kdf(salt)
        dek = bytearray(secrets.token_bytes(DEK_LEN))
        kek = _derive_kek(passphrase, salt, KDF_TIME_COST, KDF_MEMORY_COST, KDF_PARALLELISM)
        try:
            n1 = secrets.token_bytes(NONCE_LEN)
            wrapped = n1 + AESGCM(bytes(kek)).encrypt(n1, bytes(dek), _dek_aad(kdf, VERSION))
        finally:
            _zero(kek)
        n2 = secrets.token_bytes(NONCE_LEN)
        name_enc = n2 + AESGCM(bytes(dek)).encrypt(n2, name.encode("utf-8"), _NAME_AAD)
        header = {
            "format": FORMAT,
            "version": VERSION,
            "label": (label or "").strip(),
            "name_enc": _b64e(name_enc),
            "kdf": kdf,
            "wrapped_dek": _b64e(wrapped),
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }
        _atomic_write(path / HEADER_FILE, json.dumps(header, indent=2).encode("utf-8"))
        v = cls(path, header, dek, name)
        v._index = []
        v._save_index()
        return v

    @classmethod
    def open(cls, path: Path, passphrase: str) -> "Vault":
        """Unlock an existing vault. Raises WrongPassphrase / VaultError."""
        path = Path(path)
        header = cls._read_header(path)
        kdf = header["kdf"]
        salt = _b64d(kdf["salt"])
        kek = _derive_kek(passphrase, salt, kdf["time_cost"], kdf["memory_cost"], kdf["parallelism"])
        wrapped = _b64d(header["wrapped_dek"])
        try:
            dek = bytearray(AESGCM(bytes(kek)).decrypt(
                wrapped[:NONCE_LEN], wrapped[NONCE_LEN:], _dek_aad(kdf, header["version"])))
        except InvalidTag:
            raise WrongPassphrase("wrong passphrase") from None
        finally:
            _zero(kek)
        try:
            ne = _b64d(header["name_enc"])
            name = AESGCM(bytes(dek)).decrypt(ne[:NONCE_LEN], ne[NONCE_LEN:], _NAME_AAD).decode("utf-8")
        except InvalidTag:
            _zero(dek)
            raise VaultError("vault header has been tampered with") from None
        return cls(path, header, dek, name)

    @staticmethod
    def has_recovery_key(path: Path) -> bool:
        """True if the vault at ``path`` has a recovery wrap (no passphrase needed)."""
        try:
            h = json.loads((Path(path) / HEADER_FILE).read_text("utf-8"))
            return isinstance(h.get("recovery"), dict)
        except (OSError, ValueError, AttributeError):
            return False

    @classmethod
    def recover(cls, path: Path, code: str, new_passphrase: str) -> "Vault":
        """Unlock with the recovery code and, in the same step, re-wrap the DEK under
        ``new_passphrase`` (the old passphrase stops working). The recovery key stays
        valid; callers should suggest regenerating it. Raises WrongRecoveryCode."""
        _norm_passphrase(new_passphrase)
        secret = bytearray(_norm_recovery_code(code))
        path = Path(path)
        header = cls._read_header(path)
        rec = header.get("recovery")
        if not isinstance(rec, dict):
            _zero(secret)
            raise WrongRecoveryCode("this vault has no recovery key")
        kdf = rec["kdf"]
        kek = _derive_raw(bytes(secret), _b64d(kdf["salt"]), kdf["time_cost"], kdf["memory_cost"], kdf["parallelism"])
        _zero(secret)
        wrapped = _b64d(rec["wrapped_dek"])
        try:
            dek = bytearray(AESGCM(bytes(kek)).decrypt(
                wrapped[:NONCE_LEN], wrapped[NONCE_LEN:], _recovery_aad(kdf, header["version"])))
        except InvalidTag:
            raise WrongRecoveryCode("wrong recovery code") from None
        finally:
            _zero(kek)
        try:
            ne = _b64d(header["name_enc"])
            name = AESGCM(bytes(dek)).decrypt(ne[:NONCE_LEN], ne[NONCE_LEN:], _NAME_AAD).decode("utf-8")
        except InvalidTag:
            _zero(dek)
            raise VaultError("vault header has been tampered with") from None
        v = cls(path, header, dek, name)
        try:
            with v._lock:
                v._write_passphrase_wrap(new_passphrase)
        except BaseException:
            v.lock()          # never hand back (or leak) an unlocked vault on failure
            raise
        return v

    @staticmethod
    def _read_header(path: Path) -> dict:
        hp = path / HEADER_FILE
        try:
            header = json.loads(hp.read_text("utf-8"))
        except FileNotFoundError:
            raise VaultError("no vault at this location") from None
        except (OSError, ValueError) as e:
            raise VaultError("vault header is unreadable") from e
        return Vault.validate_header(header)

    @staticmethod
    def validate_header(header: dict) -> dict:
        """Check a header's format, KDF bounds and required fields; return it.

        Shared with alternative backends (e.g. a remote vault whose header comes
        from a server), so a tampered header can never weaken the KDF or exhaust
        memory, wherever it was stored."""
        if not isinstance(header, dict):
            raise VaultError("vault header is corrupt")
        try:
            if header["format"] != FORMAT or header["version"] != VERSION:
                raise VaultError("unsupported vault format/version")
            _check_kdf(header["kdf"])
            header["wrapped_dek"], header["name_enc"]  # noqa: B018 presence
            rec = header.get("recovery")
            if rec is not None:                     # optional; absent on older vaults
                if not isinstance(rec, dict):
                    raise VaultError("vault header is corrupt")
                _check_kdf(rec["kdf"])
                rec["wrapped_dek"]  # noqa: B018 presence
        except (KeyError, TypeError, ValueError) as e:
            raise VaultError("vault header is corrupt") from e
        return header

    def lock(self) -> None:
        """Zero the in-memory key; every later call raises VaultError."""
        with self._lock:
            _zero(self._dek)
            self._dek = None
            self._aead = None
            self._index = None

    @property
    def locked(self) -> bool:
        return self._aead is None

    def _require(self) -> AESGCM:
        a = self._aead
        if a is None:
            raise VaultError("vault is locked")
        return a

    # ---------------------------------------------------------------- properties
    @property
    def name(self) -> str:
        self._require()
        return self._name

    @property
    def path(self) -> Path:
        return self._path

    @property
    def label(self) -> str:
        return self._header.get("label", "")

    @property
    def has_recovery(self) -> bool:
        return isinstance(self._header.get("recovery"), dict)

    @property
    def recovery_created(self) -> str | None:
        rec = self._header.get("recovery")
        return rec.get("created") if isinstance(rec, dict) else None

    @property
    def write_lock(self) -> threading.RLock:
        """Held by every write; hold it to copy the vault's files consistently."""
        return self._lock

    def entry_files(self) -> list[str]:
        """File names (relative to :attr:`path`) of this vault's own files: the header,
        the encrypted index and one file per key. Stray files are not included."""
        with self._lock:
            self._require()
            names = {_file_for(k) for k in self._load_index()} | {_file_for(_INDEX_KEY)}
            return [HEADER_FILE, *sorted(n for n in names if (self._path / n).is_file())]

    # ---------------------------------------------------------------- low level
    def _encrypt(self, key: str, data: bytes) -> bytes:
        n = secrets.token_bytes(NONCE_LEN)
        return n + self._require().encrypt(n, data, key.encode("utf-8"))

    def _decrypt(self, key: str, blob: bytes) -> bytes:
        if len(blob) < NONCE_LEN + 16:
            raise VaultError("vault entry is corrupt")
        try:
            return self._require().decrypt(blob[:NONCE_LEN], blob[NONCE_LEN:], key.encode("utf-8"))
        except InvalidTag:
            raise VaultError("vault entry failed integrity check") from None

    def _raw_put(self, key: str, data: bytes) -> None:
        _atomic_write(self._path / _file_for(key), self._encrypt(key, data))

    def _raw_get(self, key: str) -> bytes | None:
        try:
            blob = (self._path / _file_for(key)).read_bytes()
        except FileNotFoundError:
            return None
        return self._decrypt(key, blob)

    def _load_index(self) -> list[str]:
        if self._index is None:
            raw = self._raw_get(_INDEX_KEY)
            self._index = json.loads(raw.decode("utf-8")) if raw is not None else []
        return self._index

    def _save_index(self) -> None:
        self._raw_put(_INDEX_KEY, json.dumps(sorted(self._index or [])).encode("utf-8"))

    # ---------------------------------------------------------------- public API
    def put(self, key: str, data: bytes) -> None:
        _check_key(key)
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes")
        with self._lock:
            self._require()
            idx = self._load_index()
            self._raw_put(key, bytes(data))
            if key not in idx:
                idx.append(key)
                self._save_index()

    def get(self, key: str) -> bytes | None:
        _check_key(key)
        with self._lock:
            self._require()
            return self._raw_get(key)

    def delete(self, key: str) -> None:
        _check_key(key)
        with self._lock:
            self._require()
            idx = self._load_index()
            try:
                os.unlink(self._path / _file_for(key))
            except FileNotFoundError:
                pass
            if key in idx:
                idx.remove(key)
                self._save_index()

    def destroy(self) -> int:
        """Permanently delete this vault from disk and lock it. Removes only the vault's
        own files (header, index, one file per key, stray temp files), then the folder
        if it is empty. Without the header the wrapped data key is gone, so any copy of
        the ciphertext left behind (backups, SSD blocks) can no longer be decrypted.
        Returns the number of files removed."""
        with self._lock:
            self._require()
            names = {_file_for(k) for k in self._load_index()} | {_file_for(_INDEX_KEY)}
            removed = 0
            for p in list(self._path.iterdir()):
                if p.is_file() and (p.name in names or (p.name.startswith(".tmp-") and p.name.endswith(".part"))):
                    p.unlink()
                    removed += 1
            hdr = self._path / HEADER_FILE
            if hdr.exists():
                hdr.unlink()
                removed += 1
            self.lock()
            try:
                self._path.rmdir()
            except OSError:   # the folder holds files that are not ours: leave them
                pass
            return removed

    def keys(self) -> list[str]:
        with self._lock:
            self._require()
            return sorted(self._load_index())

    def put_json(self, key: str, obj) -> None:
        """JSON-encode and store. Decimal/date/Enum/dataclass are converted
        (Decimal -> str, date -> ISO string); get_json returns plain JSON types."""
        data = json.dumps(obj, default=_json_default, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        self.put(key, data)

    def get_json(self, key: str, default=None):
        raw = self.get(key)
        if raw is None:
            return default
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as e:
            raise VaultError("vault entry is not valid JSON") from e

    def change_passphrase(self, old: str, new: str) -> None:
        """Verify ``old``, then re-wrap the existing DEK under a key derived
        from ``new`` (fresh salt). Data files and the recovery wrap are untouched."""
        _norm_passphrase(new)
        with self._lock:
            self._verify_passphrase(old)
            self._write_passphrase_wrap(new)

    def set_recovery_code(self, passphrase: str) -> str:
        """Verify ``passphrase``, then create (or replace) the recovery wrap and return
        the new code. The code itself is not stored; an earlier code stops working.

        The returned ``str`` cannot be scrubbed from memory: callers show it once and
        drop it, and never log it."""
        with self._lock:
            self._verify_passphrase(passphrase)
            raw = bytearray(secrets.token_bytes(RECOVERY_BYTES))
            salt = secrets.token_bytes(KDF_SALT_LEN)
            kdf = _new_kdf(salt)
            kek = _derive_raw(bytes(raw), salt, KDF_TIME_COST, KDF_MEMORY_COST, KDF_PARALLELISM)
            try:
                n = secrets.token_bytes(NONCE_LEN)
                wrapped = n + AESGCM(bytes(kek)).encrypt(n, bytes(self._dek), _recovery_aad(kdf, VERSION))
            finally:
                _zero(kek)
            code = format_recovery_code(bytes(raw))
            _zero(raw)
            rec = {"kdf": kdf, "wrapped_dek": _b64e(wrapped),
                   "created": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
            self._write_header(dict(self._header, recovery=rec))
            return code

    def remove_recovery_code(self, passphrase: str) -> None:
        """Verify ``passphrase``, then delete the recovery wrap (the code stops working)."""
        with self._lock:
            self._verify_passphrase(passphrase)
            self._write_header({k: v for k, v in self._header.items() if k != "recovery"})

    # ---------------------------------------------------------------- header writes
    def _verify_passphrase(self, passphrase: str) -> None:
        """Raise WrongPassphrase unless ``passphrase`` unwraps *this* vault's DEK."""
        self._require()
        kdf = self._header["kdf"]
        kek = _derive_kek(passphrase, _b64d(kdf["salt"]), kdf["time_cost"], kdf["memory_cost"], kdf["parallelism"])
        wrapped = _b64d(self._header["wrapped_dek"])
        try:
            check = bytearray(AESGCM(bytes(kek)).decrypt(
                wrapped[:NONCE_LEN], wrapped[NONCE_LEN:], _dek_aad(kdf, self._header["version"])))
        except InvalidTag:
            raise WrongPassphrase("wrong passphrase") from None
        finally:
            _zero(kek)
        same = secrets.compare_digest(bytes(check), bytes(self._dek))
        _zero(check)
        if not same:
            raise VaultError("vault key mismatch")

    def _write_passphrase_wrap(self, new: str) -> None:
        self._require()
        salt = secrets.token_bytes(KDF_SALT_LEN)
        new_kdf = _new_kdf(salt)
        nkek = _derive_kek(new, salt, KDF_TIME_COST, KDF_MEMORY_COST, KDF_PARALLELISM)
        try:
            n = secrets.token_bytes(NONCE_LEN)
            new_wrapped = n + AESGCM(bytes(nkek)).encrypt(n, bytes(self._dek), _dek_aad(new_kdf, VERSION))
        finally:
            _zero(nkek)
        self._write_header(dict(self._header, kdf=new_kdf, wrapped_dek=_b64e(new_wrapped), version=VERSION))

    def _write_header(self, header: dict) -> None:
        _atomic_write(self._path / HEADER_FILE, json.dumps(header, indent=2).encode("utf-8"))
        self._header = header

    def __repr__(self) -> str:  # never include key material
        return f"<Vault path={str(self._path)!r} locked={self.locked}>"

    def __enter__(self) -> "Vault":
        return self

    def __exit__(self, *exc) -> None:
        self.lock()
