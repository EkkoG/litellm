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
REMOTE_DOCKER_BIN="${REMOTE_DOCKER_BIN:-/opt/homebrew/bin/docker}"
LOCK_DIR="${TMPDIR:-/tmp}/litellm-gomo-deploy.lock"
DEPLOY_ONLY=false

usage() {
  printf '%s\n' \
    'Usage:' \
    '  release_gomo.sh' \
    '  release_gomo.sh --deploy-only'
}

cleanup() {
  rmdir "${LOCK_DIR}" 2>/dev/null || true
}

trap cleanup EXIT

case "$#" in
  0)
    ;;
  1)
    case "$1" in
      --deploy-only)
        DEPLOY_ONLY=true
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        usage >&2
        exit 1
        ;;
    esac
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac

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

echo "Latest numeric registry tag: ${latest_version}"
if [[ "${DEPLOY_ONLY}" == "true" ]]; then
  if (( latest_version < 1 )); then
    echo "Error: no numeric registry version is available to deploy" >&2
    exit 1
  fi
  release_version="${latest_version}"
else
  release_version=$((latest_version + 1))
  echo "Building and pushing ${IMAGE_REPOSITORY}:latest and ${IMAGE_REPOSITORY}:${release_version}"
  "${BUILD_SCRIPT}" \
    -r "${IMAGE_REPOSITORY}" \
    --repo-root "${REPO_ROOT}" \
    --dockerfile docker/Dockerfile.non_root \
    --platform "${PLATFORM}" \
    -v "latest,${release_version}"
fi

printf -v remote_command 'cd %q && %q pull %q && %q compose up -d' \
  "${REMOTE_COMPOSE_DIR}" \
  "${REMOTE_DOCKER_BIN}" \
  "${IMAGE_REPOSITORY}:latest" \
  "${REMOTE_DOCKER_BIN}"

echo "Deploying version ${release_version} to ${DEPLOY_TARGET}"
"${SSH_BIN}" "${DEPLOY_TARGET}" "${remote_command}"

if [[ "${DEPLOY_ONLY}" == "true" ]]; then
  echo "Deployed existing ${IMAGE_REPOSITORY}:${release_version} from latest"
else
  echo "Released ${IMAGE_REPOSITORY}:${release_version} and updated latest"
fi
