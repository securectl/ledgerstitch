# SPDX-License-Identifier: AGPL-3.0-only
import os
import stat

import pytest

from ledgerstitch.desktop import InstanceLock, _clean_argv, _instance_alive, _tiny_pdf


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock semantics")
def test_instance_lock_is_exclusive_and_private(tmp_path):
    a, b = InstanceLock(tmp_path), InstanceLock(tmp_path)
    assert a.acquire()
    assert not b.acquire()                    # second instance sees it held
    a.write({"port": 1234, "token": "t" * 32, "url": "http://127.0.0.1:1234/"})
    assert stat.S_IMODE(os.stat(a.path).st_mode) == 0o600
    assert b.read()["port"] == 1234
    a.release()
    assert b.read() is None                   # cleared on exit
    assert b.acquire()                        # and free for the next launch
    b.release()


def test_instance_alive_false_when_nothing_listens():
    assert not _instance_alive({"port": 1, "token": "x"}, timeout=0.2)


def test_clean_argv_drops_finder_psn():
    assert _clean_argv(["-psn_0_12345", "--no-browser"]) == ["--no-browser"]


def test_tiny_pdf_has_text_layer():
    pdfplumber = pytest.importorskip("pdfplumber")
    import io
    with pdfplumber.open(io.BytesIO(_tiny_pdf("Hello 12.34"))) as pdf:
        assert "12.34" in pdf.pages[0].extract_text()
