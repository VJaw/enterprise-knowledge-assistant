#!/bin/sh
# One worker: embedded Qdrant holds a file lock (§B12.3).
set -e
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-7860}" --workers 1
