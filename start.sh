#!/usr/bin/env bash
set -o errexit

PORT="${PORT:-10000}"
exec python -m gunicorn config.wsgi:application --bind "0.0.0.0:${PORT}" --workers 1 --timeout 30 --access-logfile - --error-logfile -
