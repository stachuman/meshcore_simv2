#!/usr/bin/env bash
# Thin wrapper — delegates to run_sweep.sh with "dense" variant.
exec "$(dirname "$0")/run_sweep.sh" dense "$@"
