#!/usr/bin/env bash
set -euo pipefail

MODE="all"
RESTART_GATEWAY=0
SKILLS_PREF="default"
KIT_VERSION=""
NO_SELF_UPGRADE=0
# Override only used by curl|bash bootstrap; left empty to use the default base.
KIT_DOWNLOAD_BASE_URL="${KIT_DOWNLOAD_BASE_URL:-https://skillhub-1388575217.cos.ap-guangzhou.myqcloud.com}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cli-only)
      MODE="cli"
      shift
      ;;
    --skill-only)
      MODE="skill"
      shift
      ;;
    --plugin-only)
      MODE="plugin"
      shift
      ;;
    --restart-gateway)
      RESTART_GATEWAY=1
      shift
      ;;
    --no-skills)
      SKILLS_PREF="off"
      shift
      ;;
    --with-skills)
      SKILLS_PREF="on"
      shift
      ;;
    --kit-version)
      if [[ $# -lt 2 ]]; then
        echo "Error: --kit-version requires a value (e.g. 2026.6.10)" >&2
        exit 1
      fi
      KIT_VERSION="$2"
      shift 2
      ;;
    --kit-version=*)
      KIT_VERSION="${1#--kit-version=}"
      shift
      ;;
    --no-self-upgrade)
      NO_SELF_UPGRADE=1
      shift
      ;;
    -h|--help)
      cat <<'USAGE'
Usage: install.sh [--cli-only|--skill-only|--plugin-only]
                  [--no-skills|--with-skills]
                  [--kit-version <ver>] [--no-self-upgrade]
                  [--restart-gateway]

Installs the skillhub CLI.
Default mode installs CLI + workspace skill (find-skill style).
Use --plugin-only only when you explicitly want legacy plugin injection.
Use --no-skills to skip installing workspace skills and persist this preference
for OTA self-upgrade migrations.

--kit-version <ver>
    Pin the installer to a specific kit version. When the script is executed
    remotely (e.g. via `curl ... | bash -s -- --kit-version 2026.6.17`),
    the matching tarball is downloaded as the install source. If omitted in
    remote mode, install/latest.tar.gz is used. When running from a locally
    extracted kit, this flag only sanity-checks against the bundled version.json.

--no-self-upgrade
    Persist `auto_self_upgrade=false` to ~/.skillhub/config.json so the CLI
    will not prompt or upgrade on startup. Recommended for page-install /
    pinned-version deployments where the version must remain stable.
USAGE
      exit 0
      ;;
    *)
      echo "Error: unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR_RAW=""
# When invoked via `curl ... | bash`, BASH_SOURCE is unset and `set -u` would
# trip. Probe defensively and leave SCRIPT_DIR_RAW empty in piped mode so the
# remote-download path is the only thing that runs.
if [[ -n "${BASH_SOURCE+x}" && -n "${BASH_SOURCE[0]:-}" ]]; then
  SCRIPT_DIR_RAW="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
fi
# Detect a usable local kit directory. When invoked via `curl|bash` the script
# has no companion files; in that case we need to fetch the kit by --kit-version.
detect_local_kit_dir() {
  local base="$1"
  if [[ -z "${base}" || ! -d "${base}" ]]; then
    return 1
  fi
  if [[ -f "${base}/cli/skills_store_cli.py" ]]; then
    echo "${base}"
    return 0
  fi
  if [[ -f "${base}/skills_store_cli.py" ]]; then
    echo "${base}"
    return 0
  fi
  return 1
}

KIT_ROOT=""
KIT_DOWNLOAD_DIR=""
if KIT_ROOT_DETECTED="$(detect_local_kit_dir "${SCRIPT_DIR_RAW}")"; then
  KIT_ROOT="${KIT_ROOT_DETECTED}"
fi

cleanup_download() {
  if [[ -n "${KIT_DOWNLOAD_DIR}" && -d "${KIT_DOWNLOAD_DIR}" ]]; then
    rm -rf "${KIT_DOWNLOAD_DIR}" || true
  fi
}
trap cleanup_download EXIT

fetch_remote_kit() {
  local version="$1"
  if ! command -v curl >/dev/null 2>&1; then
    echo "Error: curl is required to fetch the kit tarball." >&2
    exit 1
  fi
  if ! command -v tar >/dev/null 2>&1; then
    echo "Error: tar is required to unpack the kit tarball." >&2
    exit 1
  fi
  KIT_DOWNLOAD_DIR="$(mktemp -d -t skillhub-kit.XXXXXX)"
  local archive="${KIT_DOWNLOAD_DIR}/kit.tar.gz"
  local url
  if [[ -n "${version}" ]]; then
    # Pinned: download the version-specific tarball.
    url="${KIT_DOWNLOAD_BASE_URL}/install/skillhub-cli-${version}.tar.gz"
    # IMPORTANT: caller captures stdout as the "return value" (kit root path).
    # All progress logs MUST go to stderr to keep stdout clean.
    echo "Info: fetching kit ${version} from ${url}" >&2
  else
    # Default: fall back to the rolling latest alias maintained by update_all.sh.
    url="${KIT_DOWNLOAD_BASE_URL}/install/latest.tar.gz"
    echo "Info: --kit-version not given; fetching latest kit from ${url}" >&2
    echo "      (pin to a specific version via --kit-version <ver> if you need stability)" >&2
  fi
  if ! curl -fsSL "${url}" -o "${archive}"; then
    echo "Error: failed to download kit from ${url}" >&2
    exit 1
  fi
  local extract_dir="${KIT_DOWNLOAD_DIR}/extract"
  mkdir -p "${extract_dir}"
  if ! tar -xzf "${archive}" -C "${extract_dir}"; then
    echo "Error: failed to extract ${archive}" >&2
    exit 1
  fi
  # Allow either flat layout or a single-top-level-directory layout.
  local detected
  if detected="$(detect_local_kit_dir "${extract_dir}")"; then
    echo "${detected}"
    return 0
  fi
  local first_child
  first_child="$(find "${extract_dir}" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  if [[ -n "${first_child}" ]] && detected="$(detect_local_kit_dir "${first_child}")"; then
    echo "${detected}"
    return 0
  fi
  echo "Error: downloaded kit does not contain skills_store_cli.py" >&2
  exit 1
}

if [[ -z "${KIT_ROOT}" ]]; then
  KIT_ROOT="$(fetch_remote_kit "${KIT_VERSION}")"
fi

# Supports two archive layouts:
# 1) install.sh at kit root: ./install.sh + ./cli + ./plugin + ./skill
# 2) install.sh inside cli folder: ./cli/install.sh + ./cli/plugin + ./cli/skill + cli files
if [[ -d "${KIT_ROOT}/cli" ]]; then
  CLI_SRC_DIR="${KIT_ROOT}/cli"
  PLUGIN_SRC_DIR="${KIT_ROOT}/plugin"
  SKILL_SRC_DIR="${KIT_ROOT}/skill"
else
  CLI_SRC_DIR="${KIT_ROOT}"
  PLUGIN_SRC_DIR="${KIT_ROOT}/plugin"
  SKILL_SRC_DIR="${KIT_ROOT}/skill"
fi

# Sanity check: when both --kit-version and local kit exist, ensure they match.
if [[ -n "${KIT_VERSION}" && -f "${CLI_SRC_DIR}/version.json" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    LOCAL_KIT_VERSION="$(python3 -c 'import json,sys
try:
    print(json.load(open(sys.argv[1])).get("version",""))
except Exception:
    print("")' "${CLI_SRC_DIR}/version.json" 2>/dev/null || true)"
    if [[ -n "${LOCAL_KIT_VERSION}" && "${LOCAL_KIT_VERSION}" != "${KIT_VERSION}" ]]; then
      echo "Warn: --kit-version=${KIT_VERSION} differs from bundled version.json=${LOCAL_KIT_VERSION}; using bundled files." >&2
    fi
  fi
fi

# Resolve the effective self_update_url to seed into ~/.skillhub/config.json.
# Priority: metadata.json bundled with the kit (test packages have a rewritten URL)
# > prod default. This makes test-bucket builds self-contained.
PROD_SELF_UPDATE_URL_DEFAULT="https://skillhub-1388575217.cos.ap-guangzhou.myqcloud.com/version.json"
EFFECTIVE_SELF_UPDATE_URL="${PROD_SELF_UPDATE_URL_DEFAULT}"
if [[ -f "${CLI_SRC_DIR}/metadata.json" ]] && command -v python3 >/dev/null 2>&1; then
  META_SELF_URL="$(python3 -c 'import json,sys
try:
    raw = json.load(open(sys.argv[1]))
    val = raw.get("self_update_manifest_url") if isinstance(raw, dict) else ""
    print(val if isinstance(val, str) and val.strip() else "")
except Exception:
    print("")' "${CLI_SRC_DIR}/metadata.json" 2>/dev/null || true)"
  if [[ -n "${META_SELF_URL}" ]]; then
    EFFECTIVE_SELF_UPDATE_URL="${META_SELF_URL}"
  fi
fi
export EFFECTIVE_SELF_UPDATE_URL

INSTALL_BASE="${HOME}/.skillhub"
BIN_DIR="${HOME}/.local/bin"
CLI_TARGET="${INSTALL_BASE}/skills_store_cli.py"
UPGRADE_MODULE_TARGET="${INSTALL_BASE}/skills_upgrade.py"
VERSION_TARGET="${INSTALL_BASE}/version.json"
METADATA_TARGET="${INSTALL_BASE}/metadata.json"
INDEX_TARGET="${INSTALL_BASE}/skills_index.local.json"
CONFIG_TARGET="${INSTALL_BASE}/config.json"
WRAPPER_TARGET="${BIN_DIR}/skillhub"
LEGACY_WRAPPER_TARGET="${BIN_DIR}/oc-skills"

PLUGIN_TARGET_DIR="${HOME}/.openclaw/extensions/skillhub"
FIND_SKILL_TARGET_DIR="${HOME}/.openclaw/workspace/skills/find-skills"
PREFERENCE_SKILL_TARGET_DIR="${HOME}/.openclaw/workspace/skills/skillhub-preference"

ensure_cli_client_id() {
  CONFIG_TARGET="${CONFIG_TARGET}" python3 - <<'PY'
import json
import os
import re
import uuid
from pathlib import Path

path = Path(os.environ["CONFIG_TARGET"]).expanduser()
raw = {}
if path.exists():
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
    except Exception:
        raw = {}

client_id = str(raw.get("client_id") or "").strip()
if client_id and len(client_id) <= 128 and re.fullmatch(r"[A-Za-z0-9._-]+", client_id):
    raise SystemExit(0)

raw["client_id"] = str(uuid.uuid4())
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

find_openclaw_bin() {
  if command -v openclaw >/dev/null 2>&1; then
    command -v openclaw
    return 0
  fi
  if [[ -x "${HOME}/.local/share/pnpm/openclaw" ]]; then
    echo "${HOME}/.local/share/pnpm/openclaw"
    return 0
  fi
  return 1
}

install_cli() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is required for skillhub." >&2
    exit 1
  fi

  mkdir -p "${INSTALL_BASE}" "${BIN_DIR}"
  cp "${CLI_SRC_DIR}/skills_store_cli.py" "${CLI_TARGET}"
  cp "${CLI_SRC_DIR}/skills_upgrade.py" "${UPGRADE_MODULE_TARGET}"
  cp "${CLI_SRC_DIR}/version.json" "${VERSION_TARGET}"
  cp "${CLI_SRC_DIR}/metadata.json" "${METADATA_TARGET}"
  if [[ -f "${CLI_SRC_DIR}/skills_index.local.json" ]]; then
    cp "${CLI_SRC_DIR}/skills_index.local.json" "${INDEX_TARGET}"
  fi
  chmod +x "${CLI_TARGET}"

  if [[ ! -f "${CONFIG_TARGET}" ]]; then
    # Use printf instead of a heredoc to interpolate EFFECTIVE_SELF_UPDATE_URL.
    printf '{\n  "self_update_url": "%s"\n}\n' "${EFFECTIVE_SELF_UPDATE_URL}" > "${CONFIG_TARGET}"
  fi
  ensure_cli_client_id

  cat > "${WRAPPER_TARGET}" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail

BASE="${HOME}/.skillhub"
CLI="${BASE}/skills_store_cli.py"

if [[ ! -f "${CLI}" ]]; then
  echo "Error: CLI not found at ${CLI}" >&2
  exit 1
fi

exec python3 "${CLI}" "$@"
WRAPPER

  chmod +x "${WRAPPER_TARGET}"

  cat > "${LEGACY_WRAPPER_TARGET}" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
exec "${HOME}/.local/bin/skillhub" "$@"
WRAPPER

  chmod +x "${LEGACY_WRAPPER_TARGET}"
}

set_workspace_skills_preference() {
  local enabled="$1"
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Warn: python3 not found; cannot persist skills preference." >&2
    return 0
  fi

  python3 - "$CONFIG_TARGET" "$enabled" "$EFFECTIVE_SELF_UPDATE_URL" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1]).expanduser()
enabled = sys.argv[2].strip().lower() == "true"
default_update_url = sys.argv[3].strip()

raw = {}
if config_path.exists():
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
    except Exception:
        raw = {}

if not isinstance(raw.get("self_update_url"), str) or not raw["self_update_url"].strip():
    raw["self_update_url"] = default_update_url
raw["install_workspace_skills"] = enabled

config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

set_self_upgrade_preference() {
  local enabled="$1"
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Warn: python3 not found; cannot persist self-upgrade preference." >&2
    return 0
  fi

  python3 - "$CONFIG_TARGET" "$enabled" "$EFFECTIVE_SELF_UPDATE_URL" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1]).expanduser()
enabled = sys.argv[2].strip().lower() == "true"
default_update_url = sys.argv[3].strip()

raw = {}
if config_path.exists():
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
    except Exception:
        raw = {}

if not isinstance(raw.get("self_update_url"), str) or not raw["self_update_url"].strip():
    raw["self_update_url"] = default_update_url
raw["auto_self_upgrade"] = enabled
# Drop any stale ignored marker - the install just pinned a fresh version.
raw.pop("ignored_self_update_version", None)

config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

install_plugin() {
  mkdir -p "${PLUGIN_TARGET_DIR}"
  cp "${PLUGIN_SRC_DIR}/index.ts" "${PLUGIN_TARGET_DIR}/index.ts"
  cp "${PLUGIN_SRC_DIR}/openclaw.plugin.json" "${PLUGIN_TARGET_DIR}/openclaw.plugin.json"
}

install_skill() {
  local find_skill_src="${SKILL_SRC_DIR}/SKILL.md"
  local preference_skill_src="${SKILL_SRC_DIR}/SKILL.skillhub-preference.md"
  local installed=0

  if [[ -f "${find_skill_src}" ]]; then
    mkdir -p "${FIND_SKILL_TARGET_DIR}"
    cp "${find_skill_src}" "${FIND_SKILL_TARGET_DIR}/SKILL.md"
    installed=1
  else
    echo "Warn: find-skills source not found at ${find_skill_src}; skipped." >&2
  fi

  if [[ -f "${preference_skill_src}" ]]; then
    mkdir -p "${PREFERENCE_SKILL_TARGET_DIR}"
    cp "${preference_skill_src}" "${PREFERENCE_SKILL_TARGET_DIR}/SKILL.md"
    installed=1
  else
    echo "Warn: skillhub-preference source not found at ${preference_skill_src}; skipped." >&2
  fi

  if [[ "${installed}" -ne 1 ]]; then
    echo "Warn: no skill templates installed." >&2
  fi
}

configure_plugin() {
  local openclaw_bin
  if ! openclaw_bin="$(find_openclaw_bin)"; then
    echo "Warn: openclaw not found on PATH; skipped plugin config." >&2
    return 0
  fi

  "${openclaw_bin}" config set plugins.entries.skillhub.enabled true
  "${openclaw_bin}" config set plugins.entries.skillhub.config.primaryCli 'skillhub'
  "${openclaw_bin}" config set plugins.entries.skillhub.config.fallbackCli 'clawhub'
  "${openclaw_bin}" config set plugins.entries.skillhub.config.primaryLabel 'cn-optimized'
  "${openclaw_bin}" config set plugins.entries.skillhub.config.fallbackLabel 'public-registry'
}

disable_plugin_if_present() {
  local openclaw_bin
  if ! openclaw_bin="$(find_openclaw_bin)"; then
    echo "Warn: openclaw not found on PATH; skipped plugin disable." >&2
    return 0
  fi

  # Remove the whole config entry to avoid OpenClaw warning:
  # "plugin disabled (not in allowlist) but config is present".
  if ! "${openclaw_bin}" config unset plugins.entries.skillhub >/dev/null 2>&1; then
    echo "Info: skillhub plugin config entry not found or already removed; skip disable."
  fi
}

restart_gateway_if_needed() {
  if [[ "${RESTART_GATEWAY}" -ne 1 ]]; then
    return 0
  fi

  local openclaw_bin
  if ! openclaw_bin="$(find_openclaw_bin)"; then
    echo "Warn: openclaw not found on PATH; skipped gateway restart." >&2
    return 0
  fi

  nohup "${openclaw_bin}" gateway run --bind loopback --port 18789 --force >/tmp/openclaw-gateway.log 2>&1 &
}

if [[ "${MODE}" == "all" || "${MODE}" == "cli" ]]; then
  install_cli
fi

if [[ "${SKILLS_PREF}" == "off" ]]; then
  set_workspace_skills_preference false
elif [[ "${SKILLS_PREF}" == "on" ]]; then
  set_workspace_skills_preference true
fi

if [[ "${NO_SELF_UPGRADE}" -eq 1 ]]; then
  if [[ "${MODE}" == "all" || "${MODE}" == "cli" ]]; then
    set_self_upgrade_preference false
  else
    echo "Info: --no-self-upgrade ignored because CLI was not installed in this run."
  fi
fi

if [[ "${MODE}" == "all" || "${MODE}" == "skill" ]]; then
  if [[ "${SKILLS_PREF}" != "off" ]]; then
    install_skill
  else
    echo "Info: skipped workspace skills installation by --no-skills."
  fi
  disable_plugin_if_present
fi

if [[ "${MODE}" == "plugin" ]]; then
  install_plugin
  configure_plugin
fi

restart_gateway_if_needed

echo "Install complete."
echo "  mode: ${MODE}"
if [[ -n "${KIT_VERSION}" ]]; then
  echo "  kit-version: ${KIT_VERSION}"
fi
if [[ "${NO_SELF_UPGRADE}" -eq 1 && ( "${MODE}" == "all" || "${MODE}" == "cli" ) ]]; then
  echo "  auto_self_upgrade: disabled (--no-self-upgrade)"
fi
if [[ "${MODE}" == "all" || "${MODE}" == "cli" ]]; then
  echo "  cli: ${WRAPPER_TARGET}"
  if [[ -f "${INDEX_TARGET}" ]]; then
    echo "  index: ${INDEX_TARGET}"
  fi
fi
if [[ "${MODE}" == "all" || "${MODE}" == "skill" ]]; then
  if [[ "${SKILLS_PREF}" != "off" ]]; then
    echo "  skill: ${FIND_SKILL_TARGET_DIR}/SKILL.md"
    echo "  skill: ${PREFERENCE_SKILL_TARGET_DIR}/SKILL.md"
  else
    echo "  skill: skipped (--no-skills)"
  fi
fi
if [[ "${MODE}" == "plugin" ]]; then
  echo "  plugin: ${PLUGIN_TARGET_DIR}"
fi
echo
echo "Quick check:"
if [[ "${MODE}" == "all" || "${MODE}" == "cli" ]]; then
  echo "  skillhub search calendar"
fi
if [[ "${MODE}" == "all" || "${MODE}" == "skill" ]]; then
  if [[ "${SKILLS_PREF}" != "off" ]]; then
    echo "  test -f ${FIND_SKILL_TARGET_DIR}/SKILL.md && echo find-skills-installed"
    echo "  test -f ${PREFERENCE_SKILL_TARGET_DIR}/SKILL.md && echo skillhub-preference-installed"
  else
    echo "  skills install skipped by --no-skills"
  fi
fi
if [[ "${MODE}" == "plugin" ]]; then
  echo "  If you use OpenClaw: openclaw plugins list | grep skillhub"
fi
