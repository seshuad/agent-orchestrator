#!/usr/bin/env bash
# Copy the laptop workspace's agents, connectors and connections to the pod. The laptop keeps its own; runs and
# Build with Claude chats stay where they were. The vault (account tokens) is copied only with --with-vault.
#   deploy/gke/copy-workspace.sh [--with-vault] [workspace folder, default .workspace]
set -euo pipefail
cd "$(dirname "$0")/../.."
VAULT=0; [ "${1:-}" = "--with-vault" ] && { VAULT=1; shift; }
SRC=${1:-.workspace}
NS=agent-orchestrator
POD=$(kubectl -n "$NS" get pod -l app=agent-orchestrator -o jsonpath='{.items[0].metadata.name}')

items=()
for f in "$SRC"/*; do
  name=$(basename "$f")
  case "$name" in runs|build|vault|*-backup-*) continue ;; esac
  items+=("$name")
done
[ "$VAULT" = 1 ] && [ -d "$SRC/vault" ] && items+=(vault)
echo "Copying to $POD: ${items[*]}"
tar -C "$SRC" -czf - "${items[@]}" | kubectl -n "$NS" exec -i "$POD" -- tar -C /data/workspace -xzf -
kubectl -n "$NS" rollout restart deployment/agent-orchestrator
kubectl -n "$NS" rollout status deployment/agent-orchestrator --timeout 10m
