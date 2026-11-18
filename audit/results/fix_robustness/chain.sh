#!/bin/bash
# Runs the whole fix-robustness batch unattended (2026-11-18). Disk fits one model at a time:
# qwen3:8b runs first, is removed, then gemma3:12b is pulled and run. Log: batch.log beside this file.
cd "$(dirname "$0")/../../.."
free_gib() { df -g /System/Volumes/Data | awk 'NR==2{print $4}'; }
echo "=== start $(date -u +%FT%H:%M) free=${GIB:-$(free_gib)}GiB"
until ollama list | grep -q '^qwen3:8b '; do sleep 30; done
ollama list
audit/run_fix_robustness.sh qwen || exit 1
.venv/bin/python -m audit status --config audit/config_fixrob_native_confirm_reworded_nothink.yaml
.venv/bin/python -m audit status --config audit/config_fixrob_native_confirm_noplan_nothink.yaml
ollama rm qwen3:8b
echo "=== pulling gemma3:12b free=$(free_gib)GiB $(date -u +%H:%M)"
ollama pull gemma3:12b 2>&1 | tail -1
ollama list
[ "$(free_gib)" -ge 1 ] || { echo "LOW DISK, stopping"; exit 1; }
audit/run_fix_robustness.sh gemma || exit 1
echo "=== chain done $(date -u +%FT%H:%M) free=$(free_gib)GiB"
