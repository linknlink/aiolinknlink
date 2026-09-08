#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <user@ha-host> [ha-config-dir]" >&2
  exit 2
fi

remote_host=$1
config_dir=${2:-/var/lib/docker/volumes/homeassistant_config/_data}
repo_dir=$(cd "$(dirname "$0")/.." && pwd)
release_id=$(date -u +%Y%m%dT%H%M%SZ)
archive=$(mktemp "${TMPDIR:-/tmp}/linknlink-ha.XXXXXX")
remote_archive="/tmp/linknlink-ha-${release_id}.tgz"

python3 "$repo_dir/scripts/sync-bundled-library.py"
python3 "$repo_dir/scripts/validate-hacs.py"

cleanup() {
  rm -f "$archive"
}
trap cleanup EXIT

COPYFILE_DISABLE=1 tar \
  --format=ustar \
  --exclude='._*' \
  -czf "$archive" \
  -C "$repo_dir" \
  custom_components/linknlink \
  src/aiolinknlink \
  pyproject.toml \
  README.md

scp "$archive" "${remote_host}:${remote_archive}"
ssh "$remote_host" bash -s -- "$remote_archive" "$config_dir" "$release_id" <<'REMOTE'
set -euo pipefail

archive=$1
config_dir=$2
release_id=$3

if [[ "$config_dir" != /* || "$config_dir" == "/" ]]; then
  echo "HA config directory must be an absolute, non-root path" >&2
  exit 2
fi

stage_dir="/tmp/linknlink-ha-${release_id}"
backup_dir="${config_dir}/backups/linknlink/${release_id}"
component_dir="${config_dir}/custom_components/linknlink"
library_dir="${config_dir}/deps/aiolinknlink"

mkdir -p "$stage_dir"
tar -xzf "$archive" -C "$stage_dir"

sudo install -d "${config_dir}/custom_components" "${config_dir}/deps" "$backup_dir"
if sudo test -d "$component_dir"; then
  sudo cp -a "$component_dir" "$backup_dir/component"
else
  sudo touch "$backup_dir/no-component"
fi
if sudo test -d "$library_dir"; then
  sudo cp -a "$library_dir" "$backup_dir/library"
else
  sudo touch "$backup_dir/no-library"
fi

sudo rm -rf "${component_dir}.new" "${library_dir}.new"
sudo install -d "${component_dir}.new" "${library_dir}.new/src"
sudo cp -a "$stage_dir/custom_components/linknlink/." "${component_dir}.new/"
sudo cp -a "$stage_dir/src/aiolinknlink" "${library_dir}.new/src/"
sudo cp "$stage_dir/pyproject.toml" "$stage_dir/README.md" "${library_dir}.new/"

sudo docker exec homeassistant python -m compileall -q \
  "/config/custom_components/linknlink.new" \
  "/config/deps/aiolinknlink.new/src"

sudo rm -rf "$component_dir" "$library_dir"
sudo mv "${component_dir}.new" "$component_dir"
sudo mv "${library_dir}.new" "$library_dir"
sudo docker restart homeassistant >/dev/null

ready=false
for _attempt in {1..60}; do
  container_state=$(sudo docker inspect --format \
    '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
    homeassistant)
  if [[ "$container_state" == "healthy" || "$container_state" == "running" ]]; then
    ready=true
    break
  fi
  sleep 2
done

rm -rf "$stage_dir" "$archive"
if [[ "$ready" != true ]]; then
  echo "Home Assistant did not become ready; backup retained at: $backup_dir" >&2
  exit 1
fi
echo "Deployed LinknLink. Backup: $backup_dir"
REMOTE
