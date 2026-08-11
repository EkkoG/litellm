#!/usr/bin/env bash

get_registry_tags_json() {
  local image_repository="$1"
  local curl_bin="${CURL_BIN:-curl}"
  local jq_bin="${JQ_BIN:-jq}"
  local docker_config_dir="${DOCKER_CONFIG:-${HOME}/.docker}"
  local docker_config_file="${docker_config_dir}/config.json"
  local registry_host
  local registry_repository
  local credential_helper
  local credential_helper_bin
  local credential_json
  local registry_username
  local registry_secret
  local tags_url

  if [[ "${image_repository}" != */* ]]; then
    echo "Error: image repository must include a registry host: ${image_repository}" >&2
    return 1
  fi

  if [[ ! -f "${docker_config_file}" ]]; then
    echo "Error: Docker config not found: ${docker_config_file}" >&2
    return 1
  fi

  registry_host="${image_repository%%/*}"
  registry_repository="${image_repository#*/}"
  credential_helper="$(
    "${jq_bin}" -r --arg registry "${registry_host}" \
      '.credHelpers[$registry] // .credsStore // empty' \
      "${docker_config_file}"
  )"

  if [[ -z "${credential_helper}" ]]; then
    echo "Error: no Docker credential helper configured for ${registry_host}" >&2
    return 1
  fi

  credential_helper_bin="docker-credential-${credential_helper}"
  if ! command -v "${credential_helper_bin}" >/dev/null 2>&1; then
    echo "Error: Docker credential helper not found: ${credential_helper_bin}" >&2
    return 1
  fi

  credential_json="$(printf '%s' "${registry_host}" | "${credential_helper_bin}" get)"
  registry_username="$(printf '%s' "${credential_json}" | "${jq_bin}" -er '.Username')"
  registry_secret="$(printf '%s' "${credential_json}" | "${jq_bin}" -er '.Secret')"
  tags_url="https://${registry_host}/v2/${registry_repository}/tags/list?n=10000"

  "${curl_bin}" --fail --silent --show-error \
    --user "${registry_username}:${registry_secret}" \
    "${tags_url}"
}

get_latest_numeric_registry_tag() {
  local tags_json="$1"
  local jq_bin="${JQ_BIN:-jq}"

  printf '%s' "${tags_json}" | "${jq_bin}" -er \
    '[.tags[]? | select(test("^[0-9]+$")) | tonumber] | max // 0'
}

registry_tag_exists() {
  local tags_json="$1"
  local tag="$2"
  local jq_bin="${JQ_BIN:-jq}"

  printf '%s' "${tags_json}" | "${jq_bin}" -e --arg tag "${tag}" \
    '(.tags // []) | index($tag) != null' >/dev/null
}
