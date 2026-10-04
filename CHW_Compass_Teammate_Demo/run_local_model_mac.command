#!/bin/bash
set -e
cd "$(dirname "$0")"
export COMPASS_LOCAL_MODEL="${COMPASS_LOCAL_MODEL:-gemma3:4b}"
if ! command -v ollama >/dev/null 2>&1; then
  echo "Install and open Ollama from https://ollama.com/download/mac first."
  exit 1
fi
if ! ollama list >/dev/null 2>&1; then
  echo "Open the Ollama app first, then run this command again."
  exit 1
fi
if ! ollama show "$COMPASS_LOCAL_MODEL" >/dev/null 2>&1; then
  echo "Download the model once: ollama pull $COMPASS_LOCAL_MODEL"
  exit 1
fi
bash run_mac.command
