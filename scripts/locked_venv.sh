#!/bin/bash
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
# Make a new virtual environment holding exactly what requirements.lock names, each file
# checked against the lock's hashes, and print the path of its Python.
#   scripts/locked_venv.sh build/locked-venv
#   PYTHON=$(scripts/locked_venv.sh build/locked-venv) scripts/build_app.sh
# A build made with it ships the locked packages and nothing that happens to be in .venv.
# pip refuses a file whose hash does not match and a package the lock does not name. The
# Python is the one .venv is made from (BASE_PYTHON= names another).
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=${1:?usage: locked_venv.sh <folder>}
BASE=${BASE_PYTHON:-$(./.venv/bin/python3 -c 'import sys; print(sys._base_executable)')}

rm -rf "$DIR"
"$BASE" -m venv "$DIR"
"$DIR/bin/python3" -m pip install --quiet --disable-pip-version-check --require-hashes \
    -r requirements.lock >&2
"$DIR/bin/python3" -m pip check >&2
echo "$DIR/bin/python3"
