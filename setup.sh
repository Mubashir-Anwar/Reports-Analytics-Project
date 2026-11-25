#!/bin/sh
# One command for a new computer or server (Linux, macOS): virtual environment, packages, settings and the first sign-in.
#   sh setup.sh                     (extra words go to the setup command, e.g. sh setup.sh --ask)
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Making the virtual environment (.venv)..."
  python3 -m venv .venv
fi
echo "Installing the packages..."
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
exec .venv/bin/python -m backend.app.setup "$@"
