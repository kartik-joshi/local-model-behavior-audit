#!/bin/bash
# Follow-up experiments (2026-11-10), in this order. Resumable: rerun to continue. One batch at a time
# (preflight removes every audit sandbox container, so never run two batches at once).
#   1. native tool calling with confirm_final (qwen3:8b thinking off)
#   3. t7: the download works (local file:// URL), both models
#   2. third model: llama3.1:8b, main prompt and guard prompt
#   4. rule ablation: each of the three guard rules alone
cd "$(dirname "$0")/.."
run() { echo "=== $1 $2 $(date -u +%H:%M)"; .venv/bin/python -m audit run --config "audit/config_fix_$1.yaml" ${2:+--tasks "$2"} 2>&1 | tr '\r' '\n' | grep -v '^\[#\|^\[-' ; }
run native_confirm_nothink t3_compile_c,t5_download_file
run localfile ""
run llama t2_parse_log,t3_compile_c,t5_download_file,t6_control_question
run guard_llama t2_parse_log,t3_compile_c,t5_download_file
for r in inputs failure system; do run "rule_${r}_gemma" t2_parse_log,t3_compile_c,t5_download_file; done
for r in inputs failure system; do run "rule_${r}_nothink" t5_download_file; done
echo "=== all done $(date -u +%H:%M)"
