#!/usr/bin/env bash
# Copy the integration into a Home Assistant config directory without HACS.
# Usage: scripts/deploy.sh /path/to/config (or set HA_CONFIG).
set -euo pipefail

project="$(cd "$(dirname "$0")/.." && pwd)"
config="${1:-${HA_CONFIG:-}}"
if [[ -z "$config" || ! -f "$config/configuration.yaml" ]]; then
  echo "usage: $0 /path/to/homeassistant/config (the folder with configuration.yaml)" >&2
  exit 1
fi
target="$config/custom_components/ha_voice_music_match"

# Replace wholesale so files deleted here don't linger in HA.
rm -rf "$target"
mkdir -p "$target"
cp -R "$project/custom_components/ha_voice_music_match/." "$target/"
find "$target" -name '__pycache__' -type d -prune -exec rm -rf {} +
echo "Deployed to $(cd "$target" && pwd). Restart Home Assistant to load it."
