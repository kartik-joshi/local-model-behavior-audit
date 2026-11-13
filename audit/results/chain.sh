#!/bin/bash
# Main config (adds qwen3:8b; finished gemma runs are skipped), then the optional-tools variant.
cd "$(dirname "$0")/../.."   # project root
export PYTHONUNBUFFERED=1
.venv/bin/python -m audit run > audit/results/batch_qwen.log 2>&1 < /dev/null
echo "main exit $?" >> audit/results/chain.status
.venv/bin/python -m audit run --config audit/config_optional_tools.yaml > audit/results/optional_tools/batch.log 2>&1 < /dev/null
echo "variant exit $?" >> audit/results/chain.status
