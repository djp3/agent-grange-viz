# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""scripts/sparkle_public_key.sh prints the public half of a Sparkle key exported to a file.
None of Sparkle's tools does, and a release that signs with such a file compares it with the
app's key before it builds anything. The key used here is the one RFC 8032 gives as a test."""
import base64
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_KEY = ROOT / "scripts" / "sparkle_public_key.sh"
# RFC 8032, section 7.1, test 1: an Ed25519 seed and the public key that belongs to it.
SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
ITS_PUBLIC = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
needs_swift = pytest.mark.skipif(shutil.which("swift") is None, reason="needs the Swift toolchain, as a build does")


def public_half(tmp_path, held: bytes | str):
    key = tmp_path / "exported.key"
    key.write_text(held if isinstance(held, str) else base64.b64encode(held).decode() + "\n")
    return subprocess.run(["/bin/bash", str(PUBLIC_KEY), str(key)], capture_output=True, text=True, timeout=120)


@needs_swift
def test_an_exported_keys_public_half_is_the_one_ed25519_gives_its_seed(tmp_path):
    got = public_half(tmp_path, SEED)
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == base64.b64encode(ITS_PUBLIC).decode()
    assert base64.b64encode(SEED).decode() not in got.stdout + got.stderr                      # only the public half


@needs_swift
def test_a_key_in_sparkles_older_form_carries_its_public_half_at_its_end(tmp_path):
    got = public_half(tmp_path, bytes(64) + ITS_PUBLIC)
    assert got.returncode == 0 and got.stdout.strip() == base64.b64encode(ITS_PUBLIC).decode()


@needs_swift
@pytest.mark.parametrize("held, said", [("not a key at all\n", "the file is not base64 text"),
                                        (bytes(40), "the key is 40 bytes"), ("", "the key is 0 bytes")])
def test_what_is_not_a_sparkle_key_is_refused_and_says_why(tmp_path, held, said):
    got = public_half(tmp_path, held)
    assert got.returncode == 1 and got.stdout == "" and said in got.stderr


def test_a_key_file_that_is_not_there_is_refused(tmp_path):
    got = subprocess.run(["/bin/bash", str(PUBLIC_KEY), str(tmp_path / "nowhere.key")], capture_output=True, text=True)
    assert got.returncode == 1 and got.stdout == "" and "cannot read" in got.stderr
