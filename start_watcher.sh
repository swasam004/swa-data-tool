#!/usr/bin/env bash
# SWA Data Analysis Tool - background watcher (Linux/Mac).
cd "$(dirname "$0")"
python3 -m swa_data_tool watch --config config.yaml
