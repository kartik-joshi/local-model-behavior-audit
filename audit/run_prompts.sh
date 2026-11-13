#!/bin/bash
# Prompt-robustness batch (2026-11-11). Resumable: rerun to continue. One batch at a time
# (preflight removes every audit sandbox container, so never run two batches at once).
# Each config: t2, t3, t5, t6 at 50 runs; seeds match the main batch.
cd "$(dirname "$0")/.."
T=t2_parse_log,t3_compile_c,t5_download_file,t6_control_question
run() { echo "=== $1 $(date -u +%H:%M)"; .venv/bin/python -m audit run --config "audit/config_prompt_$1.yaml" --tasks "$T" 2>&1 | tr '\r' '\n' | grep -v '^\[#\|^\[-' ; }
for c in reworded_nothink noplan_nothink reworded noplan optional_llama; do run "$c"; done
echo "=== all done $(date -u +%H:%M)"
