#!/usr/bin/env bash
# 把 oi_scan.json 压成单行后，强制推到 GitHub 的 data 分支（永远只有一个 commit，不留历史）。
set -euo pipefail
JSON="${OI_SCAN_JSON:-/home/tou/.hermes/oi_monitor/oi_scan.json}"
REPO="${OI_REPO_DIR:-/home/tou/oi-monitor}"
BRANCH="${OI_DATA_BRANCH:-data}"
REMOTE="${OI_REMOTE:-origin}"
[ -f "$JSON" ] || { echo "no data: $JSON" >&2; exit 1; }
TMP="$(mktemp /tmp/oi_scan.min.XXXXXX.json)"
trap 'rm -f "$TMP"' EXIT
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); open(sys.argv[2],'w').write(json.dumps(d,ensure_ascii=False,separators=(',',':')))" "$JSON" "$TMP"
cd "$REPO"
blob="$(git hash-object -w "$TMP")"
tree="$(printf '100644 blob %s\toi_scan.json\n' "$blob" | git mktree)"
commit="$(git commit-tree "$tree" -m "data: oi_scan $(date -u +%Y-%m-%dT%H:%M:%SZ)")"
git push -f "$REMOTE" "$commit:refs/heads/$BRANCH" >/dev/null 2>&1
echo "pushed $commit -> $BRANCH ($(stat -c %s "$TMP") bytes)"
