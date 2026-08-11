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

grep -Fq -- 'ARG PROXY_EXTRAS_SOURCE=local' "${SCRIPT_DIR}/../docker/Dockerfile.non_root"

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

MISMATCH_ROOT="${TMP_DIR}/mismatch-repo"
MISMATCH_SCRIPT="${MISMATCH_ROOT}/scripts/release_gomo.sh"
MISMATCH_STDERR="${TMP_DIR}/mismatch.stderr"
mkdir -p \
  "${MISMATCH_ROOT}/scripts" \
  "${MISMATCH_ROOT}/litellm/proxy" \
  "${MISMATCH_ROOT}/litellm-proxy-extras/litellm_proxy_extras"
cp "${SCRIPT_DIR}/release_gomo.sh" "${MISMATCH_SCRIPT}"
cp "${SCRIPT_DIR}/gomo_registry_utils.sh" "${MISMATCH_ROOT}/scripts/gomo_registry_utils.sh"
printf '%s\n' 'root schema' > "${MISMATCH_ROOT}/schema.prisma"
printf '%s\n' 'root schema' > "${MISMATCH_ROOT}/litellm/proxy/schema.prisma"
printf '%s\n' 'different schema' > "${MISMATCH_ROOT}/litellm-proxy-extras/litellm_proxy_extras/schema.prisma"

printf '' > "${BUILD_LOG}"
printf '' > "${SSH_LOG}"

if PATH="${TMP_DIR}:${PATH}" \
  BUILD_SCRIPT="${BUILD_SCRIPT}" \
  SSH_BIN="${SSH_SCRIPT}" \
  TMPDIR="${TMP_DIR}" \
  "${MISMATCH_SCRIPT}" 2> "${MISMATCH_STDERR}"; then
  echo "Expected mismatched Prisma schemas to fail the release" >&2
  exit 1
fi

grep -Fq -- 'Prisma schema mismatch' "${MISMATCH_STDERR}"
grep -Fq -- 'litellm-proxy-extras/litellm_proxy_extras/schema.prisma' "${MISMATCH_STDERR}"
[[ ! -s "${BUILD_LOG}" ]]
[[ ! -s "${SSH_LOG}" ]]

echo "release_gomo.sh tests passed"
