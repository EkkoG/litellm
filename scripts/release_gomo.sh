#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SCRIPT_DIR}/gomo_registry_utils.sh"

BUILD_SCRIPT="${BUILD_SCRIPT:-${REPO_ROOT}/../gomo/Calico/calico-platform/build_and_push_images.sh}"
IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-hubsz.gomo.com/litellm-gomo/litellm}"
PLATFORM="${PLATFORM:-linux/arm64}"
DEPLOY_TARGET="${DEPLOY_TARGET:-dabaoji@10.10.0.11}"
REMOTE_COMPOSE_DIR="${REMOTE_COMPOSE_DIR:-/Volumes/ExtremeSSD/gomo-services}"
SSH_BIN="${SSH_BIN:-ssh}"
LOCK_DIR="${TMPDIR:-/tmp}/litellm-gomo-deploy.lock"

cleanup() {
  rmdir "${LOCK_DIR}" 2>/dev/null || true
}

trap cleanup EXIT

if [[ ! -x "${BUILD_SCRIPT}" ]]; then
  echo "Error: build script is not executable: ${BUILD_SCRIPT}" >&2
  exit 1
fi

if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  echo "Error: another release or rollback is running, or a stale lock exists: ${LOCK_DIR}" >&2
  exit 1
fi

tags_json="$(get_registry_tags_json "${IMAGE_REPOSITORY}")"
latest_version="$(get_latest_numeric_registry_tag "${tags_json}")"
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
