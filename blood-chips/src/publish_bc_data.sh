#!/usr/bin/env bash
# 抓带血筹码面板 5 个只读接口 → 单行 JSON → 强制推到 GitHub 的 data-bc 分支（每次覆盖，不留历史）。
set -uo pipefail
BASE="${BC_BASE:-http://127.0.0.1:8767}"
REPO="${OI_REPO_DIR:-/home/tou/oi-monitor}"
BRANCH="${BC_DATA_BRANCH:-data-bc}"
REMOTE="${OI_REMOTE:-origin}"
PY="${BC_PY:-/home/tou/.hermes/hermes-agent/venv/bin/python3}"
TMPD="$(mktemp -d /tmp/bcdata.XXXXXX)"
trap 'rm -rf "$TMPD"' EXIT

ok=0
for pair in "data:bc_data.json" "deep:bc_deep.json" "discount:bc_discount.json" "brief:bc_brief.json" "status:bc_status.json"; do
  api="${pair%%:*}"; file="${pair##*:}"
  if "$PY" -c "
import sys, json, urllib.request
try:
    with urllib.request.urlopen('$BASE/api/$api', timeout=30) as r:
        d = json.loads(r.read().decode('utf-8', 'replace'))
except Exception as e:
    print('skip $api:', type(e).__name__, e, file=sys.stderr); sys.exit(1)
open(sys.argv[1], 'w').write(json.dumps(d, ensure_ascii=False, separators=(',', ':')))
" "$TMPD/$file"; then
    ok=$((ok+1))
  fi
done
[ "$ok" -ge 1 ] || { echo "no data captured from $BASE" >&2; exit 1; }

cd "$REPO"
: > "$TMPD/.entries"
for f in "$TMPD"/*.json; do
  [ -f "$f" ] || continue
  b="$(git hash-object -w "$f")"
  printf '100644 blob %s\t%s\n' "$b" "$(basename "$f")" >> "$TMPD/.entries"
done
tree="$(git mktree < "$TMPD/.entries")"
commit="$(git commit-tree "$tree" -m "data(bc): blood-chips snapshot $(date -u +%Y-%m-%dT%H:%M:%SZ)")"
git push -f "$REMOTE" "$commit:refs/heads/$BRANCH" >/dev/null 2>&1
echo "pushed bc snapshot ($ok files) $commit -> $BRANCH"
