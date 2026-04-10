#!/usr/bin/env bash
# Thin wrapper — delegates to run_sweep.sh with "medium" variant.
exec "$(dirname "$0")/run_sweep.sh" medium "$@"
