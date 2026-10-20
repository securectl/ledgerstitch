# SPDX-License-Identifier: AGPL-3.0-only
"""Encrypted backups (``.lsbackup``) of a vault folder, and safe restore.

A backup is a plain zip of the vault's **already encrypted** files, so making one
needs no decryption and the file stays encrypted at rest::

    manifest.json           {"format": "ledgerstitch-backup", "version": 1, "created",
                             "files": {"vault/<name>": {"size", "sha256"}}}
    vault/vault.json        the header (KDF params, wrapped DEK(s), encrypted name)
    vault/<24 hex>.bin      one encrypted entry per key, plus the encrypted index

Opening a restored vault still needs the passphrase (or the recovery code) that
was valid when the backup was made. The manifest hashes only catch truncation or
corruption early; integrity against tampering comes from AES-GCM on every entry.

Restore treats the archive as hostile: only the member names above are accepted
(no traversal, absolute paths, directories or links), sizes are counted while
reading (not trusted from the zip headers), the header must pass
:meth:`Vault.validate_header`, and the target folder must not exist yet: a
restore never overwrites a vault. The header is written last, so a failed
restore never shows up as a half-made vault.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Union

from .vault import HEADER_FILE, Vault, VaultError

__all__ = ["BackupError", "BACKUP_SUFFIX", "create_backup", "restore_backup", "read_manifest"]

BACKUP_FORMAT = "ledgerstitch-backup"
BACKUP_VERSION = 1
BACKUP_SUFFIX = ".lsbackup"
MANIFEST = "manifest.json"
_PREFIX = "vault/"
_ENTRY_RE = re.compile(r"^vault/(?:vault\.json|[0-9a-f]{24}\.bin)$")

MAX_MEMBERS = 100_000
MAX_MEMBER_BYTES = 512 * 1024 * 1024          # one encrypted entry (big ledgers, receipt images)
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024      # whole restored vault
MAX_HEADER_BYTES = 64 * 1024
_CHUNK = 1024 * 1024


class BackupError(VaultError):
    """The backup is malformed, unsafe, too large, or the target already exists."""


# --------------------------------------------------------------------------- create
def create_backup(vault: Vault, out: Union[Path, BinaryIO]) -> dict:
    """Write an ``.lsbackup`` of the (unlocked) ``vault`` to ``out`` (a path or a
    writable binary file). Holds the vault's write lock while copying so the backup
    is a consistent snapshot. Returns the manifest."""
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    files: dict[str, dict] = {}
    with vault.write_lock:
        names = vault.entry_files()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:   # ciphertext does not compress
            for name in names:
                src = vault.path / name
                st = os.lstat(src)
                if not stat.S_ISREG(st.st_mode):
                    raise BackupError("vault folder holds an unexpected non-file entry")
                h = hashlib.sha256()
                size = 0
                arc = _PREFIX + name
                with open(src, "rb") as f, z.open(_zinfo(arc), "w", force_zip64=True) as dst:
                    for chunk in iter(lambda: f.read(_CHUNK), b""):
                        h.update(chunk)
                        size += len(chunk)
                        dst.write(chunk)
                files[arc] = {"size": size, "sha256": h.hexdigest()}
            manifest = {"format": BACKUP_FORMAT, "version": BACKUP_VERSION, "created": created,
                        "files": files}
            z.writestr(_zinfo(MANIFEST), json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def _zinfo(name: str) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, date_time=datetime.now(timezone.utc).timetuple()[:6])
    zi.compress_type = zipfile.ZIP_STORED
    zi.external_attr = (stat.S_IFREG | 0o600) << 16
    return zi


# --------------------------------------------------------------------------- inspect & restore
def _open_zip(src: Union[Path, bytes, BinaryIO]) -> zipfile.ZipFile:
    if isinstance(src, (bytes, bytearray, memoryview)):
        src = io.BytesIO(bytes(src))
    try:
        return zipfile.ZipFile(src, "r")
    except (zipfile.BadZipFile, OSError, ValueError):
        raise BackupError("this is not a LedgerStitch backup file") from None


def _check_members(z: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = z.infolist()
    if len(infos) > MAX_MEMBERS + 1:
        raise BackupError("backup has too many files")
    out: dict[str, zipfile.ZipInfo] = {}
    total = 0
    for zi in infos:
        name = zi.filename
        kind = stat.S_IFMT(zi.external_attr >> 16)    # 0 when the tool recorded no type
        if zi.is_dir() or (kind and kind != stat.S_IFREG):
            raise BackupError("backup contains a link or folder entry")
        if name != MANIFEST and not _ENTRY_RE.match(name):
            raise BackupError("backup contains an unexpected file name")
        if name in out:
            raise BackupError("backup contains a duplicate file")
        if zi.flag_bits & 0x1:
            raise BackupError("encrypted zip members are not supported")
        if zi.file_size > MAX_MEMBER_BYTES:
            raise BackupError("backup contains a file that is too large")
        total += zi.file_size
        if total > MAX_TOTAL_BYTES:
            raise BackupError("backup is too large")
        out[name] = zi
    if MANIFEST not in out or _PREFIX + HEADER_FILE not in out:
        raise BackupError("backup is missing its manifest or vault header")
    return out


def _read_small(z: zipfile.ZipFile, zi: zipfile.ZipInfo, limit: int) -> bytes:
    with z.open(zi) as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise BackupError("backup metadata is too large")
    return data


def read_manifest(src: Union[Path, bytes, BinaryIO]) -> dict:
    """Validate a backup without extracting it; return its manifest (plus ``header``,
    the parsed vault header). Raises BackupError."""
    with _open_zip(src) as z:
        return _validate(z)[0]


def _validate(z: zipfile.ZipFile) -> tuple[dict, dict[str, zipfile.ZipInfo]]:
    members = _check_members(z)
    try:
        manifest = json.loads(_read_small(z, members[MANIFEST], MAX_HEADER_BYTES * 16).decode("utf-8"))
        if manifest["format"] != BACKUP_FORMAT or manifest["version"] != BACKUP_VERSION:
            raise BackupError("unsupported backup format/version")
        files = manifest["files"]
        if not isinstance(files, dict) or set(files) != set(members) - {MANIFEST}:
            raise BackupError("backup manifest does not match its contents")
        for meta in files.values():
            int(meta["size"]), str(meta["sha256"])  # noqa: B018 presence
        header = json.loads(_read_small(z, members[_PREFIX + HEADER_FILE], MAX_HEADER_BYTES).decode("utf-8"))
    except BackupError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as e:
        raise BackupError("backup manifest is corrupt") from e
    try:
        Vault.validate_header(header)
    except VaultError as e:
        raise BackupError(f"backup header is not valid: {e}") from None
    return {**manifest, "header": header}, members


def restore_backup(src: Union[Path, bytes, BinaryIO], dest: Path) -> Path:
    """Extract a validated backup into the NEW folder ``dest`` (which must not exist)
    and return it. The vault stays locked; open it with its passphrase or recovery
    code. On any failure the partly written folder is removed."""
    dest = Path(dest)
    with _open_zip(src) as z:
        manifest, members = _validate(z)
        try:
            dest.mkdir(mode=0o700, parents=False, exist_ok=False)   # never overwrite
        except FileExistsError:
            raise BackupError("a folder already exists at the restore location") from None
        os.chmod(dest, 0o700)
        try:
            budget = MAX_TOTAL_BYTES
            # header last: until it exists, the folder is not a vault
            order = sorted(n for n in members if n != MANIFEST and n != _PREFIX + HEADER_FILE)
            for name in [*order, _PREFIX + HEADER_FILE]:
                written = _extract(z, members[name], dest / name[len(_PREFIX):],
                                   manifest["files"][name], budget)
                budget -= written
        except BaseException:
            shutil.rmtree(dest, ignore_errors=True)
            raise
    return dest


def _extract(z: zipfile.ZipFile, zi: zipfile.ZipInfo, target: Path, meta: dict, budget: int) -> int:
    """Stream one member to ``target`` (O_EXCL, no symlink following), counting bytes
    as they are read and checking them against the manifest."""
    limit = min(MAX_MEMBER_BYTES, budget)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(target), flags, 0o600)
    h = hashlib.sha256()
    size = 0
    with os.fdopen(fd, "wb") as out, z.open(zi) as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise BackupError("backup contains a file that is too large")
            h.update(chunk)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    if size != int(meta["size"]) or h.hexdigest() != meta["sha256"]:
        raise BackupError("backup is damaged (a file does not match its checksum)")
    return size
