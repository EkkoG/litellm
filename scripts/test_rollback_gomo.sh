#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${SCRIPT_DIR}/rollback_gomo.sh"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "${TMP_DIR}"
}

trap cleanup EXIT

SSH_LOG="${TMP_DIR}/ssh.log"
SSH_SCRIPT="${TMP_DIR}/ssh.sh"
CURL_SCRIPT="${TMP_DIR}/curl.sh"
TAGS_FILE="${TMP_DIR}/tags.json"
DOCKER_CONFIG_DIR="${TMP_DIR}/docker"
CREDENTIAL_HELPER="${TMP_DIR}/docker-credential-test"

printf '%s\n' '#!/usr/bin/env bash' 'printf '\''%s\n'\'' "$*" >> "${SSH_LOG}"' > "${SSH_SCRIPT}"
printf '%s\n' '#!/usr/bin/env bash' 'cat "${TAGS_FILE}"' > "${CURL_SCRIPT}"
printf '%s\n' '#!/usr/bin/env bash' 'printf '\''{"Username":"test-user","Secret":"test-secret"}\n'\''' > "${CREDENTIAL_HELPER}"
chmod +x "${SSH_SCRIPT}" "${CURL_SCRIPT}" "${CREDENTIAL_HELPER}"
mkdir -p "${DOCKER_CONFIG_DIR}"
printf '%s\n' '{"credsStore":"test"}' > "${DOCKER_CONFIG_DIR}/config.json"
printf '%s\n' '{"tags":["latest","51","52","53"]}' > "${TAGS_FILE}"

export SSH_LOG TAGS_FILE

PATH="${TMP_DIR}:${PATH}" \
SSH_BIN="${SSH_SCRIPT}" \
DOCKER_CONFIG="${DOCKER_CONFIG_DIR}" \
CURL_BIN="${CURL_SCRIPT}" \
TMPDIR="${TMP_DIR}" \
"${SCRIPT}"

grep -Fq -- 'docker pull hubsz.gomo.com/litellm-gomo/litellm:52' "${SSH_LOG}"
grep -Fq -- 'docker tag hubsz.gomo.com/litellm-gomo/litellm:52 hubsz.gomo.com/litellm-gomo/litellm:latest' "${SSH_LOG}"
grep -Fq -- 'docker compose up -d --pull never' "${SSH_LOG}"

printf '' > "${SSH_LOG}"

PATH="${TMP_DIR}:${PATH}" \
SSH_BIN="${SSH_SCRIPT}" \
DOCKER_CONFIG="${DOCKER_CONFIG_DIR}" \
CURL_BIN="${CURL_SCRIPT}" \
TMPDIR="${TMP_DIR}" \
"${SCRIPT}" --version 51

grep -Fq -- 'docker pull hubsz.gomo.com/litellm-gomo/litellm:51' "${SSH_LOG}"

printf '' > "${SSH_LOG}"

if PATH="${TMP_DIR}:${PATH}" \
  SSH_BIN="${SSH_SCRIPT}" \
  DOCKER_CONFIG="${DOCKER_CONFIG_DIR}" \
  CURL_BIN="${CURL_SCRIPT}" \
  TMPDIR="${TMP_DIR}" \
  "${SCRIPT}" 50; then
  echo "Expected a missing rollback tag to fail" >&2
  exit 1
fi

[[ ! -s "${SSH_LOG}" ]]

echo "rollback_gomo.sh tests passed"
