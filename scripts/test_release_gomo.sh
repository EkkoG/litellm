#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${SCRIPT_DIR}/release_gomo.sh"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "${TMP_DIR}"
}

trap cleanup EXIT

BUILD_LOG="${TMP_DIR}/build.log"
SSH_LOG="${TMP_DIR}/ssh.log"
BUILD_SCRIPT="${TMP_DIR}/build.sh"
SSH_SCRIPT="${TMP_DIR}/ssh.sh"
FAIL_BUILD_SCRIPT="${TMP_DIR}/fail-build.sh"
CURL_SCRIPT="${TMP_DIR}/curl.sh"
TAGS_FILE="${TMP_DIR}/tags.json"
DOCKER_CONFIG_DIR="${TMP_DIR}/docker"
CREDENTIAL_HELPER="${TMP_DIR}/docker-credential-test"

printf '%s\n' '#!/usr/bin/env bash' 'printf '\''%s\n'\'' "$*" >> "${BUILD_LOG}"' > "${BUILD_SCRIPT}"
printf '%s\n' '#!/usr/bin/env bash' 'printf '\''%s\n'\'' "$*" >> "${SSH_LOG}"' > "${SSH_SCRIPT}"
printf '%s\n' '#!/usr/bin/env bash' 'exit 42' > "${FAIL_BUILD_SCRIPT}"
printf '%s\n' '#!/usr/bin/env bash' 'cat "${TAGS_FILE}"' > "${CURL_SCRIPT}"
printf '%s\n' '#!/usr/bin/env bash' 'printf '\''{"Username":"test-user","Secret":"test-secret"}\n'\''' > "${CREDENTIAL_HELPER}"
chmod +x "${BUILD_SCRIPT}" "${SSH_SCRIPT}" "${FAIL_BUILD_SCRIPT}" "${CURL_SCRIPT}" "${CREDENTIAL_HELPER}"
mkdir -p "${DOCKER_CONFIG_DIR}"
printf '%s\n' '{"credsStore":"test"}' > "${DOCKER_CONFIG_DIR}/config.json"
printf '%s\n' '{"tags":["latest","12","53","v99"]}' > "${TAGS_FILE}"

export BUILD_LOG SSH_LOG TAGS_FILE

PATH="${TMP_DIR}:${PATH}" \
BUILD_SCRIPT="${BUILD_SCRIPT}" \
SSH_BIN="${SSH_SCRIPT}" \
DOCKER_CONFIG="${DOCKER_CONFIG_DIR}" \
CURL_BIN="${CURL_SCRIPT}" \
TMPDIR="${TMP_DIR}" \
"${SCRIPT}"

grep -Fq -- '-v latest,54' "${BUILD_LOG}"
grep -Fq -- 'dabaoji@10.10.0.11' "${SSH_LOG}"
grep -Fq -- 'docker pull hubsz.gomo.com/litellm-gomo/litellm:latest' "${SSH_LOG}"
grep -Fq -- 'docker compose up -d' "${SSH_LOG}"

printf '%s\n' '{"tags":["latest","12","53","54","v99"]}' > "${TAGS_FILE}"

PATH="${TMP_DIR}:${PATH}" \
BUILD_SCRIPT="${BUILD_SCRIPT}" \
SSH_BIN="${SSH_SCRIPT}" \
DOCKER_CONFIG="${DOCKER_CONFIG_DIR}" \
CURL_BIN="${CURL_SCRIPT}" \
TMPDIR="${TMP_DIR}" \
"${SCRIPT}"

grep -Fq -- '-v latest,55' "${BUILD_LOG}"

printf '' > "${BUILD_LOG}"
printf '' > "${SSH_LOG}"

PATH="${TMP_DIR}:${PATH}" \
BUILD_SCRIPT="${BUILD_SCRIPT}" \
SSH_BIN="${SSH_SCRIPT}" \
DOCKER_CONFIG="${DOCKER_CONFIG_DIR}" \
CURL_BIN="${CURL_SCRIPT}" \
TMPDIR="${TMP_DIR}" \
"${SCRIPT}" --deploy-only

[[ ! -s "${BUILD_LOG}" ]]
grep -Fq -- '/opt/homebrew/bin/docker pull hubsz.gomo.com/litellm-gomo/litellm:latest' "${SSH_LOG}"
grep -Fq -- '/opt/homebrew/bin/docker compose up -d' "${SSH_LOG}"

FAIL_SSH_LOG="${TMP_DIR}/fail-ssh.log"
printf '%s\n' '{"tags":["latest","60"]}' > "${TAGS_FILE}"

if PATH="${TMP_DIR}:${PATH}" \
  BUILD_SCRIPT="${FAIL_BUILD_SCRIPT}" \
  SSH_LOG="${FAIL_SSH_LOG}" \
  SSH_BIN="${SSH_SCRIPT}" \
  DOCKER_CONFIG="${DOCKER_CONFIG_DIR}" \
  CURL_BIN="${CURL_SCRIPT}" \
  TMPDIR="${TMP_DIR}" \
  "${SCRIPT}"; then
  echo "Expected failed build to fail the release" >&2
  exit 1
fi

[[ ! -e "${FAIL_SSH_LOG}" ]]

echo "release_gomo.sh tests passed"
