#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# timelapse_assemble.sh — Assemble JPEG snapshots into an mp4 time-lapse
# ─────────────────────────────────────────────────────────────────────────────
# Requires: ffmpeg (apt install ffmpeg / brew install ffmpeg)
#
# Usage:
#   bash timelapse_assemble.sh /var/lib/bosch-timelapse/outdoor
#   bash timelapse_assemble.sh /var/lib/bosch-timelapse/outdoor 24 timelapse.mp4
#
# Arguments:
#   $1  INPUT_DIR   — directory containing *.jpg snapshots (default: ./snapshots)
#   $2  FRAMERATE   — frames per second in the output video (default: 24)
#   $3  OUTPUT_FILE — output mp4 path (default: <INPUT_DIR>_timelapse.mp4)
#
# ── Framerate guide ───────────────────────────────────────────────────────────
#   Capture interval  |  24 fps output  |  12 fps output
#   ─────────────────────────────────────────────────────
#   Every 10 min      |  1 h → 0.25 s   |  1 h → 0.5 s
#   Every hour        |  1 day → 0.67 s |  1 day → 1.3 s
#   Every 15 min      |  1 day → 4 s    |  1 day → 8 s
#   Every day         |  1 year → 15 s  |  1 year → 30 s
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

INPUT_DIR="${1:-./snapshots}"
FRAMERATE="${2:-24}"
OUTPUT_FILE="${3:-${INPUT_DIR%/}_timelapse.mp4}"

if [[ ! -d "${INPUT_DIR}" ]]; then
    echo "ERROR: Input directory not found: ${INPUT_DIR}" >&2
    exit 1
fi

JPG_COUNT="$(find "${INPUT_DIR}" -maxdepth 1 -name '*.jpg' | wc -l)"
if [[ "${JPG_COUNT}" -eq 0 ]]; then
    echo "ERROR: No *.jpg files found in ${INPUT_DIR}" >&2
    exit 1
fi

echo "Assembling ${JPG_COUNT} frames at ${FRAMERATE} fps → ${OUTPUT_FILE}"
echo "Estimated duration: $(echo "scale=1; ${JPG_COUNT} / ${FRAMERATE}" | bc) seconds"

# ── Main ffmpeg command ───────────────────────────────────────────────────────
# -pattern_type glob + sorted glob: frames are ordered by filename (timestamps).
# scale=1920:-2: scale to 1080p width, height auto (keeps aspect ratio, even height).
# format=yuv420p: widest player compatibility (iOS, Android, browsers).
# libx264 + crf 22: visually lossless at reasonable file size. Raise to 28 for
#   smaller files, lower to 18 for archival quality.
# -preset slow: better compression than default "medium"; adds a few seconds of
#   encode time but cuts output file size ~20%.
ffmpeg \
    -framerate "${FRAMERATE}" \
    -pattern_type glob \
    -i "${INPUT_DIR}/*.jpg" \
    -vf "scale=1920:-2,format=yuv420p" \
    -c:v libx264 \
    -crf 22 \
    -preset slow \
    -movflags +faststart \
    "${OUTPUT_FILE}"

echo ""
echo "Done: ${OUTPUT_FILE}"
ls -lh "${OUTPUT_FILE}"

# ── Optional: open in the default video player ────────────────────────────────
# Uncomment the line for your OS:
# open "${OUTPUT_FILE}"             # macOS
# xdg-open "${OUTPUT_FILE}"        # Linux
