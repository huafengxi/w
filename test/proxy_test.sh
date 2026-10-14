#!/bin/bash
# Isolated in-process WSGI integration; no deployed instance/config is used.
set -eu
exec "${PYTHON:-python3}" "$(dirname "$0")/proxy_integration.py"
