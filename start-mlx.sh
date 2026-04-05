#!/bin/bash
# Start MLX VLM server for local model inference
# Only needed if using the MLX backend (e.g. Gemma 4)
#
# Install: pip install mlx-vlm
# Download model: huggingface-cli download mlx-community/gemma-4-26b-a4b-it-4bit
#
# Requires Apple Silicon Mac with 24GB+ RAM

cd "$(dirname "$0")"
[ -d "venv" ] && source venv/bin/activate
exec python3 -m mlx_vlm.server \
    --model mlx-community/gemma-4-26b-a4b-it-4bit \
    --port 8800 \
    --host 127.0.0.1
