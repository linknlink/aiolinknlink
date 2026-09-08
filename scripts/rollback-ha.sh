#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "Usage: $0 <user@ha-host> <backup-id> [ha-config-dir]" >&2
  exit 2
fi

remote_host=$1
backup_id=$2
config_dir=${3:-/var/lib/docker/volumes/homeassistant_config/_data}

ssh "$remote_host" bash -s -- "$backup_id" "$config_dir" <<'REMOTE'
set -euo pipefail

backup_id=$1
config_dir=$2

if [[ "$config_dir" != /* || "$config_dir" == "/" ]]; then
  echo "HA config directory must be an absolute, non-root path" >&2
  exit 2
fi

backup_dir="${config_dir}/backups/linknlink/${backup_id}"
component_dir="${config_dir}/custom_components/linknlink"
library_dir="${config_dir}/deps/aiolinknlink"

if ! sudo test -d "$backup_dir"; then
  echo "Backup does not exist: $backup_dir" >&2
  exit 1
fi

if sudo test -d "$backup_dir/component"; then
  sudo rm -rf "$component_dir"
  sudo cp -a "$backup_dir/component" "$component_dir"
elif sudo test -f "$backup_dir/no-component"; then
  sudo rm -rf "$component_dir"
fi
if sudo test -d "$backup_dir/library"; then
  sudo rm -rf "$library_dir"
  sudo cp -a "$backup_dir/library" "$library_dir"
elif sudo test -f "$backup_dir/no-library"; then
  sudo rm -rf "$library_dir"
fi

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

if [[ "$ready" != true ]]; then
  echo "Rollback restored the files, but Home Assistant did not become ready" >&2
  exit 1
fi
echo "Rolled back LinknLink from: $backup_dir"
REMOTE
