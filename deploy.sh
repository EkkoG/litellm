#!/usr/bin/env bash

set -euo pipefail

REGISTRY_BASE=""
SERVICES="litellm"
SERVICE_DIR_MAP_RAW="litellm=."
SERVICE_DOCKERFILE_MAP_RAW="litellm=docker/Dockerfile.non_root"
SERVICE_CONTEXT_MAP_RAW="litellm=."
SERVICE_IMAGE_MAP_RAW=""
REPO_ROOT="${REPO_ROOT:-}"
PUSH_IMAGE=true
VERSION=""
PLATFORM="linux/amd64"
BUILD_ARGS=()
EXTRA_BUILD_ARGS=()

usage() {
  cat <<'EOF'
Usage:
  deploy.sh [options] <version>
  deploy.sh [options] --version <version>

Options:
  -v, --version <version>    Image tag(s), e.g. v1.0.0 or v1.0.0,latest
  -r, --registry <registry>  Registry base, e.g. ghcr.io/your-user
  -s, --services <list>      Comma-separated services (default: litellm)
      --service-dir-map <m>  Service-to-dir map: litellm=.
      --service-dockerfile-map <m> Service-to-Dockerfile map: litellm=docker/Dockerfile.non_root
      --service-context-map <m>    Service-to-context map: litellm=.
      --service-image-map <m>      Service-to-image-name map: litellm=litellm
      --repo-root <path>     Repo root (default: directory containing this script)
      --platform <platform>  Target platform(s) (default: linux/amd64)
      --build-arg <arg>      Build arg passed to docker buildx, repeatable
      --no-push              Build only, do not push. Requires a single platform
  -h, --help                 Show help
EOF
}

script_dir() {
  local source_path="${BASH_SOURCE[0]}"
  local source_dir
  source_dir="$(cd "$(dirname "${source_path}")" && pwd)"
  echo "${source_dir}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -v|--version)
      [[ $# -lt 2 ]] && { echo "Error: missing value for $1"; usage; exit 1; }
      VERSION="$2"
      shift 2
      ;;
    -r|--registry)
      [[ $# -lt 2 ]] && { echo "Error: missing value for $1"; usage; exit 1; }
      REGISTRY_BASE="$2"
      shift 2
      ;;
    -s|--services)
      [[ $# -lt 2 ]] && { echo "Error: missing value for $1"; usage; exit 1; }
      SERVICES="$2"
      shift 2
      ;;
    --service-dir-map)
      [[ $# -lt 2 ]] && { echo "Error: missing value for $1"; usage; exit 1; }
      SERVICE_DIR_MAP_RAW="$2"
      shift 2
      ;;
    --service-dockerfile-map)
      [[ $# -lt 2 ]] && { echo "Error: missing value for $1"; usage; exit 1; }
      SERVICE_DOCKERFILE_MAP_RAW="$2"
      shift 2
      ;;
    --service-context-map)
      [[ $# -lt 2 ]] && { echo "Error: missing value for $1"; usage; exit 1; }
      SERVICE_CONTEXT_MAP_RAW="$2"
      shift 2
      ;;
    --service-image-map)
      [[ $# -lt 2 ]] && { echo "Error: missing value for $1"; usage; exit 1; }
      SERVICE_IMAGE_MAP_RAW="$2"
      shift 2
      ;;
    --repo-root)
      [[ $# -lt 2 ]] && { echo "Error: missing value for $1"; usage; exit 1; }
      REPO_ROOT="$2"
      shift 2
      ;;
    --platform)
      [[ $# -lt 2 ]] && { echo "Error: missing value for $1"; usage; exit 1; }
      PLATFORM="$2"
      shift 2
      ;;
    --build-arg)
      [[ $# -lt 2 ]] && { echo "Error: missing value for $1"; usage; exit 1; }
      BUILD_ARGS+=("$2")
      shift 2
      ;;
    --no-push)
      PUSH_IMAGE=false
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -z "${VERSION}" ]]; then
        VERSION="$1"
        shift
      else
        echo "Error: unknown argument: $1"
        usage
        exit 1
      fi
      ;;
  esac
done

if [[ -z "${REPO_ROOT}" ]]; then
  REPO_ROOT="$(script_dir)"
fi

if [[ -z "${VERSION}" ]]; then
  echo "Error: version is required."
  usage
  exit 1
fi

if [[ -z "${REGISTRY_BASE}" ]]; then
  echo "Error: --registry is required."
  usage
  exit 1
fi

if [[ -z "${SERVICES}" ]]; then
  echo "Error: --services cannot be empty."
  usage
  exit 1
fi

if [[ ! -d "${REPO_ROOT}" ]]; then
  echo "Error: repo root not found: ${REPO_ROOT}"
  exit 1
fi
REPO_ROOT="$(cd "${REPO_ROOT}" && pwd)"

get_map_value() {
  local target_key="$1"
  local raw_map="$2"
  local default_value="$3"
  local pair
  local key
  local value

  if [[ -n "${raw_map}" ]]; then
    IFS=',' read -r -a MAPPING_LIST <<< "${raw_map}"
    for pair in "${MAPPING_LIST[@]}"; do
      pair="${pair//[[:space:]]/}"
      [[ -z "${pair}" ]] && continue
      key="${pair%%=*}"
      value="${pair#*=}"
      if [[ "${key}" == "${target_key}" ]]; then
        echo "${value}"
        return 0
      fi
    done
  fi

  echo "${default_value}"
}

resolve_path() {
  local path_value="$1"

  if [[ "${path_value}" = /* ]]; then
    echo "${path_value}"
    return 0
  fi

  echo "${REPO_ROOT}/${path_value}"
}

validate_map_format() {
  local raw_map="$1"
  local map_name="$2"
  local raw_mapping
  local mapping
  local map_service
  local map_value

  [[ -z "${raw_map}" ]] && return 0

  IFS=',' read -r -a MAPPING_LIST <<< "${raw_map}"
  for raw_mapping in "${MAPPING_LIST[@]}"; do
    mapping="${raw_mapping//[[:space:]]/}"
    [[ -z "${mapping}" ]] && continue
    if [[ "${mapping}" != *=* ]]; then
      echo "Error: invalid mapping '${mapping}' in ${map_name}, expected service=value"
      exit 1
    fi
    map_service="${mapping%%=*}"
    map_value="${mapping#*=}"
    if [[ -z "${map_service}" || -z "${map_value}" ]]; then
      echo "Error: invalid mapping '${mapping}' in ${map_name}, expected service=value"
      exit 1
    fi
  done
}

validate_map_format "${SERVICE_DIR_MAP_RAW}" "--service-dir-map"
validate_map_format "${SERVICE_DOCKERFILE_MAP_RAW}" "--service-dockerfile-map"
validate_map_format "${SERVICE_CONTEXT_MAP_RAW}" "--service-context-map"
validate_map_format "${SERVICE_IMAGE_MAP_RAW}" "--service-image-map"

IFS=',' read -r -a SERVICE_LIST <<< "${SERVICES}"
IFS=',' read -r -a RAW_TAG_LIST <<< "${VERSION}"
TAG_LIST=()
BUILT_IMAGES=()

VALID_SERVICE_COUNT=0
for raw_service in "${SERVICE_LIST[@]}"; do
  service="${raw_service//[[:space:]]/}"
  [[ -z "${service}" ]] && continue
  VALID_SERVICE_COUNT=$((VALID_SERVICE_COUNT + 1))
done

if [[ "${VALID_SERVICE_COUNT}" -eq 0 ]]; then
  echo "Error: no valid services found in --services: ${SERVICES}"
  exit 1
fi

for raw_tag in "${RAW_TAG_LIST[@]}"; do
  tag="${raw_tag//[[:space:]]/}"
  [[ -z "${tag}" ]] && continue
  TAG_LIST+=("${tag}")
done

if [[ "${#TAG_LIST[@]}" -eq 0 ]]; then
  echo "Error: no valid tags found in version: ${VERSION}"
  exit 1
fi

if [[ -z "${PLATFORM}" ]]; then
  echo "Error: --platform cannot be empty."
  exit 1
fi

if ! docker buildx version >/dev/null 2>&1; then
  echo "Error: docker buildx is required, but buildx is not available."
  exit 1
fi

if [[ "${PUSH_IMAGE}" != "true" && "${PLATFORM}" == *,* ]]; then
  echo "Error: multi-platform build with --no-push is not supported because buildx --load only supports one platform."
  exit 1
fi

for build_arg in "${BUILD_ARGS[@]}"; do
  EXTRA_BUILD_ARGS+=(--build-arg "${build_arg}")
done

for raw_service in "${SERVICE_LIST[@]}"; do
  service="${raw_service//[[:space:]]/}"
  [[ -z "${service}" ]] && continue

  service_dir="$(get_map_value "${service}" "${SERVICE_DIR_MAP_RAW}" "${service}")"
  base_dir="$(resolve_path "${service_dir}")"
  dockerfile_override="$(get_map_value "${service}" "${SERVICE_DOCKERFILE_MAP_RAW}" "")"
  context_override="$(get_map_value "${service}" "${SERVICE_CONTEXT_MAP_RAW}" "")"
  image_name="$(get_map_value "${service}" "${SERVICE_IMAGE_MAP_RAW}" "${service}")"

  if [[ -n "${dockerfile_override}" ]]; then
    dockerfile="$(resolve_path "${dockerfile_override}")"
  else
    dockerfile="${base_dir}/Dockerfile"
  fi

  if [[ -n "${context_override}" ]]; then
    context_dir="$(resolve_path "${context_override}")"
  else
    context_dir="${base_dir}"
  fi

  echo "Resolved ${service}: image=${REGISTRY_BASE}/${image_name}, dockerfile=${dockerfile}, context=${context_dir}, platform=${PLATFORM}"

  if [[ ! -f "${dockerfile}" ]]; then
    echo "Error: Dockerfile not found for service '${service}': ${dockerfile}"
    exit 1
  fi

  if [[ ! -d "${context_dir}" ]]; then
    echo "Error: build context not found for service '${service}': ${context_dir}"
    exit 1
  fi

  buildx_cmd=(docker buildx build --platform "${PLATFORM}" -f "${dockerfile}")
  buildx_cmd+=("${EXTRA_BUILD_ARGS[@]}")
  for tag in "${TAG_LIST[@]}"; do
    image="${REGISTRY_BASE}/${image_name}:${tag}"
    buildx_cmd+=(-t "${image}")
    BUILT_IMAGES+=("${image}")
  done

  if [[ "${PUSH_IMAGE}" == "true" ]]; then
    echo "Buildx building and pushing ${service}"
    buildx_cmd+=(--push)
  else
    echo "Buildx building and loading ${service}"
    buildx_cmd+=(--load)
  fi

  buildx_cmd+=("${context_dir}")
  "${buildx_cmd[@]}"
done

if [[ "${#BUILT_IMAGES[@]}" -eq 0 ]]; then
  echo "Error: no valid services to process."
  exit 1
fi

echo "Done."
if [[ "${PUSH_IMAGE}" == "true" ]]; then
  echo "Pushed:"
else
  echo "Built:"
fi

for image in "${BUILT_IMAGES[@]}"; do
  echo "  - ${image}"
done
