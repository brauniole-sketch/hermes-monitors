#!/usr/bin/env bash
# 扫一轮币安合约 OI，然后把 oi_scan.json 推到 GitHub 的 data 分支（供 GitHub Pages 静态页读取）。
PY="${OI_PY:-/home/tou/.hermes/hermes-agent/venv/bin/python3}"
SCAN="${OI_SCAN_SCRIPT:-/home/tou/.hermes/scripts/oi_scan.py}"
PUB="${OI_PUBLISH_SCRIPT:-/home/tou/.hermes/scripts/publish_oi_data.sh}"
LOG="${OI_SCAN_LOG:-/home/tou/.hermes/oi_monitor/oi_scan.log}"
mkdir -p "$(dirname "$LOG")"
{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) scan start ==="
  "$PY" "$SCAN" 2>&1 | tail -3
  echo "=== publish ==="
  bash "$PUB" 2>&1 | tail -3
  echo "=== done $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
} >> "$LOG" 2>&1
