# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared test setup: no test ever touches the real settings, and none reaches the network.
A grange or a Docker a test talks to is a stand-in on this machine (standins.py)."""
import socket

import pytest

LOOPBACK = ("127.0.0.1", "localhost", "::1")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Settings are read from and written to a folder of the test's own."""
    monkeypatch.setenv("AGENT_GRANGE_VIZ_SETTINGS", str(tmp_path / "settings.json"))
    yield tmp_path


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """A test may talk to this machine only (a stand-in grange on 127.0.0.1). Anything else
    is a mistake in the test, and fails loudly instead of reaching out."""
    real = socket.create_connection

    def local_only(address, *args, **kwargs):
        host = address[0]
        if host not in LOOPBACK:
            raise AssertionError(f"a test tried to reach {host}")
        return real(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", local_only)
