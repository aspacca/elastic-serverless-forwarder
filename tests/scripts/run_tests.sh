#!/usr/bin/env bash

set -e

# delete any __pycache__ folders to avoid hard-to-debug caching issues
find . -name __pycache__ -type d -exec rm -r {} +
py.test -v "${PYTEST_ARGS}" "${PYTEST_JUNIT}" tests
