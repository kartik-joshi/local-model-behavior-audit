#!/bin/bash
# Fix robustness (2026-11-18): the guard rules (gemma3:12b) and native tool calling with confirm_final
# (qwen3:8b, thinking off) under the reworded and no-PLAN prompts. 50 runs per cell, seeds match the main
# batch. Resumable: rerun to continue. One batch at a time (preflight removes every audit sandbox container).
# Usage: audit/run_fix_robustness.sh qwen | gemma     (one model at a time; disk space only fits one)
cd "$(dirname "$0")/.."
run() {
  echo "=== selftest $(date -u +%H:%M)"; .venv/bin/python -m audit selftest || { echo "SELFTEST FAILED"; exit 1; }
  echo "=== $1 $2 $(date -u +%H:%M)"
  .venv/bin/python -m audit run --config "audit/config_fixrob_$1.yaml" --tasks "$2" 2>&1 | tr '\r' '\n' | grep -v '^\[#\|^\[-'
}
case "$1" in
  qwen)
    run native_confirm_reworded_nothink t3_compile_c,t5_download_file
    run native_confirm_noplan_nothink t3_compile_c,t5_download_file ;;
  gemma)  # t3 first (the headline measure), t2 last (slowest)
    run guard_reworded_gemma t3_compile_c,t5_download_file
    run guard_noplan_gemma t3_compile_c,t5_download_file
    run guard_reworded_gemma t2_parse_log
    run guard_noplan_gemma t2_parse_log ;;
  *) echo "usage: $0 qwen|gemma"; exit 2 ;;
esac
echo "=== $1 done $(date -u +%H:%M)"
