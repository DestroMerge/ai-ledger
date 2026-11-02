#!/usr/bin/env sh
# Starts the local ledger web view and opens it in the browser. Ctrl+C stops it.
exec python3 "$(dirname "$0")/ledger_web.py" "$@"
