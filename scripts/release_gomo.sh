#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_SCRIPT="${BUILD_SCRIPT:-${REPO_ROOT}/../gomo/Calico/calico-platform/build_and_push_images.sh}"
IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-hubsz.gomo.com/litellm-gomo/litellm}"
PLATFORM="${PLATFORM:-linux/arm64}"
DEPLOY_TARGET="${DEPLOY_TARGET:-dabaoji@10.10.0.11}"
REMOTE_COMPOSE_DIR="${REMOTE_COMPOSE_DIR:-/Volumes/ExtremeSSD/gomo-services}"
SSH_BIN="${SSH_BIN:-ssh}"
CURL_BIN="${CURL_BIN:-curl}"
JQ_BIN="${JQ_BIN:-jq}"
DOCKER_CONFIG_DIR="${DOCKER_CONFIG:-${HOME}/.docker}"
LOCK_DIR="${TMPDIR:-/tmp}/litellm-gomo-release.lock"

cleanup() {
  rmdir "${LOCK_DIR}" 2>/dev/null || true
}

trap cleanup EXIT

if [[ ! -x "${BUILD_SCRIPT}" ]]; then
  echo "Error: build script is not executable: ${BUILD_SCRIPT}" >&2
  exit 1
fi

if [[ "${IMAGE_REPOSITORY}" != */* ]]; then
  echo "Error: image repository must include a registry host: ${IMAGE_REPOSITORY}" >&2
  exit 1
fi

if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  echo "Error: another release is running, or a stale lock exists: ${LOCK_DIR}" >&2
  exit 1
fi

docker_config_file="${DOCKER_CONFIG_DIR}/config.json"
if [[ ! -f "${docker_config_file}" ]]; then
  echo "Error: Docker config not found: ${docker_config_file}" >&2
  exit 1
fi

registry_host="${IMAGE_REPOSITORY%%/*}"
registry_repository="${IMAGE_REPOSITORY#*/}"
credential_helper="$(
  "${JQ_BIN}" -r --arg registry "${registry_host}" \
    '.credHelpers[$registry] // .credsStore // empty' \
    "${docker_config_file}"
)"

if [[ -z "${credential_helper}" ]]; then
  echo "Error: no Docker credential helper configured for ${registry_host}" >&2
  exit 1
fi

credential_helper_bin="docker-credential-${credential_helper}"
if ! command -v "${credential_helper_bin}" >/dev/null 2>&1; then
  echo "Error: Docker credential helper not found: ${credential_helper_bin}" >&2
  exit 1
fi

credential_json="$(printf '%s' "${registry_host}" | "${credential_helper_bin}" get)"
registry_username="$(printf '%s' "${credential_json}" | "${JQ_BIN}" -er '.Username')"
registry_secret="$(printf '%s' "${credential_json}" | "${JQ_BIN}" -er '.Secret')"
tags_url="https://${registry_host}/v2/${registry_repository}/tags/list?n=10000"
tags_json="$(
  "${CURL_BIN}" --fail --silent --show-error \
    --user "${registry_username}:${registry_secret}" \
    "${tags_url}"
)"
latest_version="$(
  printf '%s' "${tags_json}" | "${JQ_BIN}" -er \
    '[.tags[]? | select(test("^[0-9]+$")) | tonumber] | max // 0'
)"
next_version=$((latest_version + 1))

echo "Latest numeric registry tag: ${latest_version}"
echo "Building and pushing ${IMAGE_REPOSITORY}:latest and ${IMAGE_REPOSITORY}:${next_version}"
"${BUILD_SCRIPT}" \
  -r "${IMAGE_REPOSITORY}" \
  --repo-root "${REPO_ROOT}" \
  --dockerfile docker/Dockerfile.non_root \
  --platform "${PLATFORM}" \
  -v "latest,${next_version}"

printf -v remote_command 'cd %q && docker pull %q && docker compose up -d' \
  "${REMOTE_COMPOSE_DIR}" \
  "${IMAGE_REPOSITORY}:latest"

echo "Deploying version ${next_version} to ${DEPLOY_TARGET}"
"${SSH_BIN}" "${DEPLOY_TARGET}" "${remote_command}"

echo "Released ${IMAGE_REPOSITORY}:${next_version} and updated latest"
