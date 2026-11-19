# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The grange's local API as the viewer reads it: answers, the long-poll, the three kinds of
"no" (nothing answers, image out of date, anything else), and that it only ever reads."""
import time

import pytest

from grangeapi import ApiError, GrangeApi, Unreachable
from standins import STALE_BODY, FakeGrange, closed_port, entry


@pytest.fixture
def grange():
    g = FakeGrange("djp3")
    yield g
    g.close()


def test_reads_health_version_and_the_picture(grange):
    api = GrangeApi(grange.port)
    assert api.health().body["grange"] == "djp3"
    assert api.version().body["commit"].startswith("bf6e87e")
    reply = api.agents()
    assert reply.status == 200 and not reply.stale
    assert reply.body["granges"][0]["grange"] == "djp3"


def test_long_poll_waits_on_an_equal_cursor_and_answers_a_change_at_once(grange):
    api = GrangeApi(grange.port)
    cursor = api.agents().body["cursor"]
    start = time.monotonic()
    same = api.agents(after=cursor, wait=0.3)
    assert 0.25 <= time.monotonic() - start < 2.0          # held for the wait, then the same picture
    assert same.body["cursor"] == cursor
    grange.set_granges([entry("alice")])
    start = time.monotonic()
    changed = api.agents(after=cursor, wait=5)
    assert time.monotonic() - start < 1.0                   # the cursor differs: no waiting (R5.7)
    assert changed.body["cursor"] != cursor
    assert [g["grange"] for g in changed.body["granges"]] == ["djp3", "alice"]


def test_the_socket_outlasts_the_wait(grange):
    api = GrangeApi(grange.port, timeout=0.2)
    cursor = api.agents().body["cursor"]
    assert api.agents(after=cursor, wait=0.5).status == 200  # 0.5 s held with a 0.2 s timeout


def test_mesh_is_none_until_the_grange_has_it(grange):
    api = GrangeApi(grange.port)
    assert api.mesh() is None
    grange.mesh = {"grange": "djp3", "peers": []}
    assert api.mesh().body["grange"] == "djp3"


def test_a_stale_image_is_a_state_not_an_error(grange):
    grange.stale = STALE_BODY
    api = GrangeApi(grange.port)
    for reply in (api.health(), api.agents(), api.version(), api.mesh()):
        assert reply.stale and reply.status == 503
        assert reply.body["tag_commit"].startswith("c0ffee")
        assert reply.body["fix"] == "make update"


def test_nothing_listening_is_unreachable():
    api = GrangeApi(closed_port(), timeout=0.5)
    with pytest.raises(Unreachable):
        api.health()
    with pytest.raises(Unreachable):
        api.agents(after=3, wait=1)


def test_a_grange_that_stops_is_unreachable(grange):
    api = GrangeApi(grange.port, timeout=0.5)
    assert api.health().status == 200
    grange.close()
    with pytest.raises(Unreachable):
        api.health()


def test_anything_else_is_an_api_error(grange):
    api = GrangeApi(grange.port)
    grange.error = 500
    with pytest.raises(ApiError) as e:
        api.health()
    assert e.value.status == 500
    grange.error = None
    with pytest.raises(ApiError) as e:
        api._get("/nowhere")
    assert e.value.status == 404 and "no route" in e.value.message


def test_it_only_reads(grange):
    api = GrangeApi(grange.port)
    api.health(), api.version(), api.agents(), api.agents(after=1, wait=0), api.mesh()
    assert grange.wrote() == []
    assert not hasattr(api, "farm")
