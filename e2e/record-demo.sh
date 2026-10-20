#!/usr/bin/env bash
# Record the /demo tour at 1920×1080 and encode it to demo/anti-radar-demo.mp4.
#
#   BASE_URL=http://localhost:3000 ./record-demo.sh          # default BASE_URL
#   DEMO_PACE=1.1 DEMO_DWELL="brief=18" ./record-demo.sh     # slower overall / per scene
#   SKIP_RECORD=1 ./record-demo.sh                           # re-encode the last take only
#
# 1. Runs demo-tour.spec.ts with RECORD=1 (paced, smooth scrolls, Playwright video at 1920×1080).
# 2. Trims the warm-up visit (the take starts on the title card's entrance; see demo-tour-meta.json).
# 3. Encodes H.264 / yuv420p / 30 fps / faststart with the static ffmpeg shipped in the imageio-ffmpeg
#    Python package, installed into a throwaway uv environment (nothing is added to any project).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
export BASE_URL="${BASE_URL:-http://localhost:3000}"
OUT="${OUT:-$ROOT/demo/anti-radar-demo.mp4}"

cd "$HERE"
if [ ! -d node_modules/@playwright/test ]; then
  npm ci --no-audit --no-fund
fi
# Same host-library shim as shot.mjs: Chromium libs missing on this machine live in ~/.local/chromelibs.
export LD_LIBRARY_PATH="$HOME/.local/chromelibs/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

if ! curl -fsS -o /dev/null --max-time 120 "$BASE_URL/demo"; then
  echo "record-demo: $BASE_URL/demo is not reachable — start the frontend (and backend) first." >&2
  exit 1
fi

if [ "${SKIP_RECORD:-0}" = "1" ]; then
  echo "record-demo: SKIP_RECORD=1 — re-encoding the last take in test-results/"
else
  echo "record-demo: recording the tour against $BASE_URL …"
  RECORD=1 npx playwright test -c playwright.config.ts
fi

META="$HERE/test-results/demo-tour-meta.json"
[ -f "$META" ] || { echo "record-demo: $META missing (did the tour run with RECORD=1?)" >&2; exit 1; }
read -r VIDEO START < <(node -e 'const m=require(process.argv[1]); console.log(m.video, Math.max(0, m.take_start_s + 0.2).toFixed(2))' "$META")
# Playwright moves the video into the test's output folder once the context closes; take the newest .webm.
if [ ! -f "$VIDEO" ]; then
  VIDEO="$(find "$HERE/test-results" -name '*.webm' -printf '%T@ %p\n' | sort -nr | head -n1 | cut -d' ' -f2-)"
fi
[ -n "$VIDEO" ] && [ -f "$VIDEO" ] || { echo "record-demo: no recorded video under test-results/" >&2; exit 1; }

FFMPEG="$(uv run --quiet --no-project --with imageio-ffmpeg python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
mkdir -p "$(dirname "$OUT")"
echo "record-demo: encoding $VIDEO (from ${START}s) → $OUT"
"$FFMPEG" -hide_banner -loglevel error -y -ss "$START" -i "$VIDEO" \
  -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p -r 30 -movflags +faststart -an "$OUT"

uv run --quiet --no-project --with imageio-ffmpeg python - "$OUT" <<'PY'
import os, sys
import imageio_ffmpeg
path = sys.argv[1]
frames, secs = imageio_ffmpeg.count_frames_and_secs(path)
print(f"record-demo: {path}  {int(secs // 60)}:{secs % 60:04.1f}  {frames} frames  {os.path.getsize(path) / 1e6:.1f} MB")
PY
