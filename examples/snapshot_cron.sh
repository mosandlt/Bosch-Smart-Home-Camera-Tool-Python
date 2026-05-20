#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# snapshot_cron.sh — Scheduled snapshot capture via cron
# ─────────────────────────────────────────────────────────────────────────────
# Wrapper for the Bosch Smart Home Camera Python CLI that saves a dated
# snapshot to a configurable output directory. Designed to be called by cron
# with zero interaction.
#
# Usage (manual test):
#   bash snapshot_cron.sh Outdoor
#   bash snapshot_cron.sh Indoor /custom/path
#
# Crontab examples (crontab -e):
#   # Every 10 minutes, all day — adapt CAM_NAME and OUTPUT_DIR below:
#   */10 * * * * /path/to/examples/snapshot_cron.sh Outdoor >> /var/log/bosch_snap.log 2>&1
#
#   # Every hour on the full hour, 06:00–22:00:
#   0 6-22 * * * /path/to/examples/snapshot_cron.sh Outdoor >> /var/log/bosch_snap.log 2>&1
#
#   # Once a day at 07:30:
#   30 7 * * * /path/to/examples/snapshot_cron.sh Outdoor >> /var/log/bosch_snap.log 2>&1
#
#   # All cameras, every 15 minutes:
#   */15 * * * * /path/to/examples/snapshot_cron.sh ALL >> /var/log/bosch_snap.log 2>&1
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
# Camera name as recognised by the CLI (partial match, case-insensitive).
# Use "ALL" to snapshot every registered camera in one run.
CAM_NAME="${1:-Outdoor}"

# Root output directory. A sub-folder per camera name is created automatically.
OUTPUT_DIR="${2:-/var/lib/bosch-timelapse}"

# Path to bosch_camera.py. Adjust if you installed it somewhere else.
BOSCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BOSCH_PY="${BOSCH_DIR}/bosch_camera.py"

# Python interpreter. Prefer the venv if it exists alongside bosch_camera.py.
if [[ -x "${BOSCH_DIR}/.venv/bin/python3" ]]; then
    PYTHON="${BOSCH_DIR}/.venv/bin/python3"
elif [[ -x "${BOSCH_DIR}/venv/bin/python3" ]]; then
    PYTHON="${BOSCH_DIR}/venv/bin/python3"
else
    PYTHON="$(command -v python3)"
fi
# ── End configuration ─────────────────────────────────────────────────────────

TS="$(date +%Y%m%d_%H%M)"
DATE="$(date +%Y%m%d)"

# Build the output filename template. One folder per camera name keeps
# the directory tidy and makes glob patterns for ffmpeg straightforward.
if [[ "${CAM_NAME}" == "ALL" ]]; then
    # When snapping all cameras the CLI writes one file per camera.
    # Use --output as a directory prefix; filenames come from the camera name.
    OUT_DIR="${OUTPUT_DIR}/all/${DATE}"
    mkdir -p "${OUT_DIR}"
    echo "[${TS}] Snapping all cameras → ${OUT_DIR}/"
    "${PYTHON}" "${BOSCH_PY}" snapshot --live --output "${OUT_DIR}/${TS}_%s.jpg"
else
    # Single camera: one JPEG per run, named by timestamp.
    CAM_SLUG="${CAM_NAME,,}"          # lowercase slug for folder name
    CAM_SLUG="${CAM_SLUG// /_}"       # spaces → underscores
    OUT_DIR="${OUTPUT_DIR}/${CAM_SLUG}"
    mkdir -p "${OUT_DIR}"
    OUTFILE="${OUT_DIR}/${TS}.jpg"
    echo "[${TS}] Snapping ${CAM_NAME} → ${OUTFILE}"
    "${PYTHON}" "${BOSCH_PY}" liveshot "${CAM_NAME}" --output "${OUTFILE}"
fi

echo "[${TS}] Done."
