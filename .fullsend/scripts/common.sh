#!/usr/bin/env bash
set -euo pipefail
case "${CI_DEBUG_TRACE:-false}" in
  1|[tT]|[tT][rR][uU][eE]) echo 'Debug tracing is prohibited for Scribe.' >&2; exit 1 ;;
esac
umask 077
SCRIBE_SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export PYTHONPATH="${SCRIBE_SCRIPT_DIR}"
: "${SCRIBE_WORKSPACE_DIR:?Set a private host workspace outside the artifact directory}"
SCRIBE_PYTHON="${SCRIBE_PYTHON:-${SCRIBE_WORKSPACE_DIR}/.venv/bin/python}"
if [[ ! -x "$SCRIBE_PYTHON" ]]; then
  python3 -m venv "${SCRIBE_WORKSPACE_DIR}/.venv"
  "$SCRIBE_PYTHON" -m pip install --quiet -r "${SCRIBE_SCRIPT_DIR}/requirements.txt"
fi
