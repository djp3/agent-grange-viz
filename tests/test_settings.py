# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings load tolerantly and save where they are told to."""
import json
import os

from settings import Settings


def test_defaults_when_there_is_no_file():
    s = Settings.load()
    assert (s.grange_port, s.use_docker, s.docker_socket) == (7788, True, "")


def test_saved_settings_come_back(tmp_path):
    s = Settings(grange_port=17981, use_docker=False, docker_socket="/tmp/docker.sock")
    s.save()
    assert Settings.load() == s
    assert json.loads((tmp_path / "settings.json").read_text())["grange_port"] == 17981


def test_the_module_loads_and_saves_them_too(tmp_path):
    """main.py holds the module, not the class: settings.load() and settings.save(s)."""
    import settings
    kept = Settings(grange_port=17982, use_docker=False)
    settings.save(kept)
    assert settings.load() == kept == Settings.load()


def test_wrong_types_and_unknown_keys_are_ignored(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps(
        {"grange_port": "7788", "use_docker": 0, "docker_socket": "/var/run/docker.sock", "surprise": 1}))
    s = Settings.load()
    assert (s.grange_port, s.use_docker, s.docker_socket) == (7788, True, "/var/run/docker.sock")


def test_true_is_not_a_port(tmp_path):
    """bool is an int to Python; a file that says `true` must not become port 1."""
    (tmp_path / "settings.json").write_text(json.dumps({"grange_port": True}))
    assert Settings.load().grange_port == 7788


def test_the_port_stays_a_tcp_port(tmp_path):
    """Nothing can answer on port 0 or past 65535: the default is used and the log says so."""
    for written, kept in ((0, 7788), (-5, 7788), (1, 1), (17981, 17981), (65535, 65535), (65536, 7788)):
        (tmp_path / "settings.json").write_text(json.dumps({"grange_port": written}))
        assert Settings.load().grange_port == kept, written


def test_a_port_out_of_range_is_logged(tmp_path, caplog):
    (tmp_path / "settings.json").write_text(json.dumps({"grange_port": 70000}))
    Settings.load()
    assert "grange_port 70000" in caplog.text and "using 7788" in caplog.text


def test_a_broken_file_gives_defaults(tmp_path):
    (tmp_path / "settings.json").write_text("{not json")
    assert Settings.load() == Settings()


def test_a_file_that_is_not_an_object_gives_defaults(tmp_path):
    (tmp_path / "settings.json").write_text("[7788]")
    assert Settings.load() == Settings()


def files(folder):
    return {p.name: p.read_text() for p in folder.iterdir()}


def test_a_save_leaves_the_file_and_nothing_beside_it(tmp_path):
    Settings(grange_port=7799).save()
    assert sorted(files(tmp_path)) == ["settings.json"]
    assert files(tmp_path)["settings.json"].endswith("}\n")


def test_a_save_that_cannot_finish_keeps_the_last_file_whole_and_leaves_nothing_behind(tmp_path, monkeypatch,
                                                                                        caplog):
    settings = Settings(grange_port=7799)
    settings.save()
    before = files(tmp_path)

    def refuse(src, dst):                                       # a read-only folder, a full disk
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(os, "replace", refuse)
    settings.grange_port = 7800
    settings.save()
    assert files(tmp_path) == before                            # the same file, as it was
    assert caplog.text.count("could not write") == 1
    assert settings.grange_port == 7800                         # the app goes on with it
