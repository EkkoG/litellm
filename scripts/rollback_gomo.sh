#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/gomo_registry_utils.sh"

IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-hubsz.gomo.com/litellm-gomo/litellm}"
DEPLOY_TARGET="${DEPLOY_TARGET:-dabaoji@10.10.0.11}"
REMOTE_COMPOSE_DIR="${REMOTE_COMPOSE_DIR:-/Volumes/ExtremeSSD/gomo-services}"
SSH_BIN="${SSH_BIN:-ssh}"
LOCK_DIR="${TMPDIR:-/tmp}/litellm-gomo-deploy.lock"
requested_version=""

usage() {
  printf '%s\n' \
    'Usage:' \
    '  rollback_gomo.sh' \
    '  rollback_gomo.sh <version>' \
    '  rollback_gomo.sh --version <version>'
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
      -h|--help)
        usage
        exit 0
        ;;
      *)
        requested_version="$1"
        ;;
    esac
    ;;
  2)
    if [[ "$1" != "-v" && "$1" != "--version" ]]; then
      usage >&2
      exit 1
    fi
    requested_version="$2"
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac

if [[ -n "${requested_version}" && ! "${requested_version}" =~ ^[0-9]+$ ]]; then
  echo "Error: rollback version must be a non-negative integer: ${requested_version}" >&2
  exit 1
fi

if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  echo "Error: another release or rollback is running, or a stale lock exists: ${LOCK_DIR}" >&2
  exit 1
fi

tags_json="$(get_registry_tags_json "${IMAGE_REPOSITORY}")"
latest_version="$(get_latest_numeric_registry_tag "${tags_json}")"

if [[ -n "${requested_version}" ]]; then
  target_version="${requested_version}"
else
  if (( latest_version < 1 )); then
    echo "Error: no previous numeric version is available" >&2
    exit 1
  fi
  target_version=$((latest_version - 1))
fi

if ! registry_tag_exists "${tags_json}" "${target_version}"; then
  echo "Error: registry tag does not exist: ${IMAGE_REPOSITORY}:${target_version}" >&2
  exit 1
fi

target_image="${IMAGE_REPOSITORY}:${target_version}"
latest_image="${IMAGE_REPOSITORY}:latest"
printf -v remote_command \
  'cd %q && docker pull %q && docker tag %q %q && docker compose up -d --pull never && docker compose ps' \
  "${REMOTE_COMPOSE_DIR}" \
  "${target_image}" \
  "${target_image}" \
  "${latest_image}"

echo "Latest numeric registry tag: ${latest_version}"
echo "Rolling back ${DEPLOY_TARGET} to ${target_image}"
"${SSH_BIN}" "${DEPLOY_TARGET}" "${remote_command}"

echo "Rolled back ${DEPLOY_TARGET} to ${target_image}"
echo "Registry tag ${latest_image} was not changed"
