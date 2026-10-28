#!/bin/bash
# prepare_render.sh — Copy project manifest + assets into render-remotion/public/
# so Remotion can render the correct theme/images/title for each video.
#
# Usage:
#   ./prepare_render.sh <project_id>
#
# Run this BEFORE `./render_full.sh <slug> <total-frames>`.

set -euo pipefail

REPO="/Users/isamumatsuyama/Documents/online-projects/red-sun-japan/channel-redsunjapan"
MOTOR="$REPO/motor-python"
REMOTION="$REPO/render-remotion"
PUBLIC="$REMOTION/public"

if [ -z "${1:-}" ]; then
  echo "Usage: $0 <project_id>"
  exit 1
fi

PROJECT_ID="$1"
STORAGE_DIR="$MOTOR/storage/$PROJECT_ID"
MANIFEST="$STORAGE_DIR/manifest.json"

if [ ! -f "$MANIFEST" ]; then
  echo "❌ No manifest found at: $MANIFEST"
  echo "   Generate it first, e.g.:"
  echo "   curl -X POST http://127.0.0.1:8771/projects/$PROJECT_ID/render-manifest \\"
  echo "     -H 'Content-Type: application/json' -d '{\"language_code\":\"en\"}'"
  exit 1
fi

mkdir -p "$PUBLIC"

echo "=== Preparing render for project $PROJECT_ID ==="

cp "$MANIFEST" "$PUBLIC/manifest.json"
echo "✅ manifest.json copied to public/"

AUDIO=$(python3 -c "import json; m=json.load(open('$MANIFEST')); print(m.get('audio_filename',''))")
TIMESTAMPS=$(python3 -c "import json; m=json.load(open('$MANIFEST')); print(m.get('timestamps_filename',''))")
IMAGES=$(python3 -c "import json; m=json.load(open('$MANIFEST')); print('\n'.join(m.get('image_filenames',[])))")
BGM=$(python3 -c "import json; m=json.load(open('$MANIFEST')); print(m.get('bgm_filename') or '')")

if [ -n "$AUDIO" ] && [ -f "$STORAGE_DIR/$AUDIO" ]; then
  cp "$STORAGE_DIR/$AUDIO" "$PUBLIC/$AUDIO"
  echo "✅ audio: $AUDIO"
else
  echo "⚠️  audio missing: ${AUDIO:-'(not set in manifest)'}"
fi

if [ -n "$TIMESTAMPS" ] && [ -f "$STORAGE_DIR/$TIMESTAMPS" ]; then
  cp "$STORAGE_DIR/$TIMESTAMPS" "$PUBLIC/timestamps.json"
  echo "✅ timestamps: $TIMESTAMPS"
else
  echo "⚠️  timestamps missing: ${TIMESTAMPS:-'(not set in manifest)'}"
fi

if [ -n "$IMAGES" ]; then
  COUNT=0
  while IFS= read -r img; do
    [ -z "$img" ] && continue
    src=$(find "$MOTOR/assets_library" -name "$img" 2>/dev/null | head -1 || true)
    if [ -n "$src" ] && [ -f "$src" ]; then
      cp "$src" "$PUBLIC/$img"
      COUNT=$((COUNT + 1))
    else
      echo "⚠️  image not found: $img"
    fi
  done <<< "$IMAGES"
  echo "✅ images copied: $COUNT"
else
  echo "⚠️  no images in manifest"
fi

if [ -n "$BGM" ]; then
  bgm_src=$(find "$MOTOR/music_library" -name "$BGM" 2>/dev/null | head -1 || true)
  if [ -n "$bgm_src" ] && [ -f "$bgm_src" ]; then
    cp "$bgm_src" "$PUBLIC/$BGM"
    echo "✅ BGM: $BGM"
  else
    echo "⚠️  BGM not found: $BGM"
  fi
fi

echo "=== Ready to render ==="
echo "Next:"
echo "  cd $REMOTION"
echo "  ./render_full.sh <slug> <total-frames>"
