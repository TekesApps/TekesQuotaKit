#!/bin/sh
# Run with the deployment database URL and token secret exported.
set -eu
if [ "$#" -ne 1 ]; then
    echo "Usage: scripts/install.sh PRIVATE_CREDENTIAL_DIRECTORY" >&2
    exit 2
fi
case "$1" in
    /*) directory=$1 ;;
    *) directory=$PWD/$1 ;;
esac
cd "$(dirname "$0")/.."
uv sync --frozen --no-dev
uv run --frozen --no-dev tekes-quota-kit init-schema
uv run --frozen --no-dev tekes-quota-kit admin-user bootstrap --credentials-dir "$directory"
