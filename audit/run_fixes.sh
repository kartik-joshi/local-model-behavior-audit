#!/bin/bash
# Fix experiments, most important first. Resumable: rerun to continue. One batch at a time.
cd "$(dirname "$0")/.."
run() { echo "=== $1 $2 $(date -u +%H:%M)"; .venv/bin/python -m audit run --config "audit/config_fix_$1.yaml" --tasks "$2" 2>&1 | tr '\r' '\n' | grep -v '^\[#\|^\[-' ; }
run native_nothink t2_parse_log,t3_compile_c,t4_summarize_notes,t5_download_file
run readonly_gemma t2_parse_log,t3_compile_c
run guard_gemma t2_parse_log,t3_compile_c,t5_download_file
run guard_nothink t4_summarize_notes,t5_download_file
run gate_gemma t3_compile_c,t5_download_file
run native_think t3_compile_c,t5_download_file
echo "=== all done $(date -u +%H:%M)"
