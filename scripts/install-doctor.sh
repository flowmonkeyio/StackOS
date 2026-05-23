#!/usr/bin/env bash
#
# Post-install doctor wrapper: tolerate daemon-down on first install, but fail
# hard on migration, token, seed, or credential problems.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCTOR_SCRIPT="${STACKOS_DOCTOR_SCRIPT:-${REPO_ROOT}/scripts/doctor.sh}"

"${DOCTOR_SCRIPT}"
code=$?

if [[ "${code}" -eq 0 ]]; then
    exit 0
fi

if [[ "${code}" -eq 1 ]]; then
    echo "  doctor: daemon is not running yet; run \`make serve\` or \`stackos start\` next."
    exit 0
fi

echo "  doctor: blocking setup issue (exit code ${code}); fix it before continuing." >&2
exit "${code}"
