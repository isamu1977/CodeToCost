#!/bin/bash
# render_full.sh — Full render pipeline for ONE video project.
#
# Expected project layout:
#   render-remotion/projects/<slug>/
#     manifest.json
#     audio_en.wav
#     timestamps_en.json
#     assets/
#       intro.mp4
#       ending.mp4
#     chunks/
#     outputs/
#
# Usage:
#   ./render_full.sh <project_id> <slug> <total-frames> [intro-mp4-path] [ending-mp4-path]
#
# Examples:
#   ./render_full.sh 1 yuki-onna-the-snow-woman-of-northern-japan 22667
#   ./render_full.sh 1 yuki-onna-the-snow-woman-of-northern-japan 22667 \
#       /path/to/intro.mp4 \
#       /path/to/ending.mp4
#
# Notes:
#   - If intro/ending paths are omitted or already live under projects/<slug>/assets/,
#     the script will use them in place without copying.

set -euo pipefail

if [ $# -lt 3 ]; then
  echo "Usage: $0 <project_id> <slug> <total-frames> [intro-mp4-path] [ending-mp4-path]"
  exit 1
fi

PROJECT_ID="$1"
SLUG="$2"
TOTAL_FRAMES="$3"
INTRO_SRC="${4:-}"
ENDING_SRC="${5:-}"

REPO="/Users/isamumatsuyama/Documents/online-projects/red-sun-japan/project-files/channel-redsunjapan"
REMOTION="$REPO/render-remotion"
PROJECT_DIR="$REMOTION/projects/$SLUG"
PUBLIC="$REMOTION/public"
CHUNK_SIZE=900
CONCURRENCY=2

mkdir -p "$PROJECT_DIR/assets" "$PROJECT_DIR/chunks" "$PROJECT_DIR/outputs"

echo "============================================="
echo "  Red Sun Japan — Per-Video Render Pipeline"
echo "============================================="
echo "  Project ID: $PROJECT_ID"
echo "  Slug: $SLUG"
echo "  Total frames: $TOTAL_FRAMES"
echo "  Project dir: $PROJECT_DIR"
echo ""

# ── Step 1: Prepare assets via prepare_render.sh ─────────────────────────
echo "=== Step 1/5: Preparing assets ==="
if [ -x "$REMOTION/prepare_render.sh" ]; then
  "$REMOTION/prepare_render.sh" "$PROJECT_ID"
else
  echo "⚠️  prepare_render.sh not found; ensure public/manifest.json + assets are present"
fi
echo ""

# ── Step 2: Render main video in chunks ──────────────────────────────────
echo "=== Step 2/5: Rendering main video ($TOTAL_FRAMES frames, chunked) ==="
CHUNKS=$(( (TOTAL_FRAMES + CHUNK_SIZE - 1) / CHUNK_SIZE ))
CONCAT_LIST="$PROJECT_DIR/.concat_render.txt"
> "$CONCAT_LIST"

cd "$REMOTION"

for i in $(seq 0 $((CHUNKS - 1))); do
    START=$((i * CHUNK_SIZE))
    END=$((START + CHUNK_SIZE - 1))
    if [ "$END" -ge "$TOTAL_FRAMES" ]; then
        END=$((TOTAL_FRAMES - 1))
    fi
    CHUNK_FILE="$PROJECT_DIR/chunks/chunk_${START}.mp4"

    if [ -f "$CHUNK_FILE" ]; then
        echo "  [$((i+1))/$CHUNKS] Skipping chunk_${START}.mp4 (exists)"
        echo "file '$CHUNK_FILE'" >> "$CONCAT_LIST"
        continue
    fi

    echo "  [$((i+1))/$CHUNKS] Rendering frames $START-$END..."
    npx remotion render RedSunJapanVideo "$CHUNK_FILE" \
        --frames=${START}-${END} \
        --concurrency=$CONCURRENCY \
        --timeout=60000 2>&1 | tail -3 || true

    if [ ! -f "$CHUNK_FILE" ]; then
        echo "❌ Failed to render chunk_${START}.mp4"
        exit 1
    fi
    echo "file '$CHUNK_FILE'" >> "$CONCAT_LIST"
done

echo "  Concatenating $CHUNKS chunks..."
ffmpeg -y -f concat -safe 0 -i "$CONCAT_LIST" \
    -c copy -avoid_negative_ts make_zero \
    -movflags +faststart \
    "$PROJECT_DIR/outputs/${SLUG}_main.mp4"
rm -f "$CONCAT_LIST"

ls -lh "$PROJECT_DIR/outputs/${SLUG}_main.mp4"
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$PROJECT_DIR/outputs/${SLUG}_main.mp4" | xargs -I {} echo "  Duration: {}s"
echo ""

# ── Step 3: Add fade-in/out to main video ────────────────────────────────
echo "=== Step 3/5: Adding fade-in (1.5s) and fade-out (1.5s) to main ==="
MAIN_FADED="$PROJECT_DIR/outputs/${SLUG}_main_faded.mp4"
MAIN_DURATION=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$PROJECT_DIR/outputs/${SLUG}_main.mp4")
FADE_OUT_START=$(python3 -c "print(max(0, $MAIN_DURATION - 1.5))")
ffmpeg -y -i "$PROJECT_DIR/outputs/${SLUG}_main.mp4" \
    -vf "fade=t=in:st=0:d=1.5,fade=t=out:st=${FADE_OUT_START}:d=1.5" \
    -af "afade=t=in:st=0:d=1.5,afade=t=out:st=${FADE_OUT_START}:d=1.5" \
    -c:v libx264 -preset fast -crf 18 -c:a aac -b:a 192k -movflags +faststart \
    "$MAIN_FADED"
echo "  ✅ Faded main video created"
echo ""

# ── Step 4: Build intro + main → final ───────────────────────────────────
echo "=== Step 4/5: Building final video (intro + main) ==="
FINAL_OUT="$PROJECT_DIR/outputs/${SLUG}_final.mp4"
INTRO_LOCAL="$PROJECT_DIR/assets/intro.mp4"

if [ -n "$INTRO_SRC" ] && [ -f "$INTRO_SRC" ]; then
    # Use provided intro; if it's already inside the project folder, don't copy
    if [ "$(realpath "$INTRO_SRC")" != "$(realpath "$INTRO_LOCAL")" ]; then
        cp "$INTRO_SRC" "$INTRO_LOCAL"
        echo "  ✅ Intro copied to project folder"
    else
        echo "  ✅ Intro already in project folder"
    fi
else
    INTRO_LOCAL=""
fi

if [ -n "$INTRO_LOCAL" ]; then
    FINAL_LIST="$PROJECT_DIR/.concat_final.txt"
    cat > "$FINAL_LIST" <<EOF
file '$INTRO_LOCAL'
file '$MAIN_FADED'
EOF
    ffmpeg -y -f concat -safe 0 -i "$FINAL_LIST" \
        -fflags +genpts \
        -c:v libx264 -preset fast -crf 18 -c:a aac -b:a 192k -movflags +faststart \
        "$FINAL_OUT"
    rm -f "$FINAL_LIST"
else
    cp "$MAIN_FADED" "$FINAL_OUT"
fi

ls -lh "$FINAL_OUT"
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$FINAL_OUT" | xargs -I {} echo "  Duration: {}s"
echo ""

# ── Step 5: Append ending → complete ────────────────────────────────────
echo "=== Step 5/5: Appending ending video ==="
COMPLETE_OUT="$PROJECT_DIR/outputs/${SLUG}_complete.mp4"
ENDING_LOCAL="$PROJECT_DIR/assets/ending.mp4"

if [ -n "$ENDING_SRC" ] && [ -f "$ENDING_SRC" ]; then
    if [ "$(realpath "$ENDING_SRC")" != "$(realpath "$ENDING_LOCAL")" ]; then
        cp "$ENDING_SRC" "$ENDING_LOCAL"
        echo "  ✅ Ending copied to project folder"
    else
        echo "  ✅ Ending already in project folder"
    fi
else
    ENDING_LOCAL=""
fi

if [ -n "$ENDING_LOCAL" ]; then
    # Normalize ending to 1920x1080, 30fps, 44100 stereo to avoid concat breakage
    ENDING_NORMALIZED="$PROJECT_DIR/assets/ending_normalized.mp4"
    ffmpeg -y -i "$ENDING_LOCAL" \
        -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:-1:-1:color=black,setsar=1,fps=30" \
        -af "aresample=44100,asetpts=PTS-STARTPTS" \
        -c:v libx264 -preset fast -crf 18 -c:a aac -b:a 192k -movflags +faststart \
        -t 5 \
        "$ENDING_NORMALIZED" || ENDING_NORMALIZED="$ENDING_LOCAL"

    COMPLETE_LIST="$PROJECT_DIR/.concat_complete.txt"
    cat > "$COMPLETE_LIST" <<EOF
file '$FINAL_OUT'
file '$ENDING_NORMALIZED'
EOF
    ffmpeg -y -f concat -safe 0 -i "$COMPLETE_LIST" \
        -fflags +genpts \
        -c:v libx264 -preset fast -crf 18 -c:a aac -b:a 192k -movflags +faststart \
        "$COMPLETE_OUT"
    rm -f "$COMPLETE_LIST"

    ls -lh "$COMPLETE_OUT"
    ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$COMPLETE_OUT" | xargs -I {} echo "  Duration: {}s"
else
    echo "  No ending provided; final video: $FINAL_OUT"
    COMPLETE_OUT="$FINAL_OUT"
fi
echo ""

echo "============================================="
echo "  ✅ DONE!"
echo "============================================="
if [ -f "$COMPLETE_OUT" ]; then
    echo "  Complete video: $COMPLETE_OUT"
    cp "$COMPLETE_OUT" "$FINAL_OUT"
    echo "  ✅ Synced complete video to $FINAL_OUT"
elif [ -f "$FINAL_OUT" ]; then
    echo "  Final video: $FINAL_OUT"
else
    echo "  Main video: $PROJECT_DIR/outputs/${SLUG}_main.mp4"
fi

PROJECT_RUNTIME="/Users/isamumatsuyama/Documents/red-sun-japan-runtime/projects/$SLUG"
CHOSEN_VIDEO="${COMPLETE_OUT:-$FINAL_OUT}"
if [ -d "$PROJECT_RUNTIME" ] && [ -f "$CHOSEN_VIDEO" ]; then
    mkdir -p "$PROJECT_RUNTIME/video"
    cp "$CHOSEN_VIDEO" "$PROJECT_RUNTIME/video/${SLUG}_master.mp4"
    rm -rf "$PROJECT_RUNTIME/output" "$PROJECT_RUNTIME/render"
    rm -f "$PROJECT_RUNTIME/video/${SLUG}_final.mp4" "$PROJECT_RUNTIME/video/output_master.mp4"
    echo "  ✅ Propagated canonical master video to: $PROJECT_RUNTIME/video/${SLUG}_master.mp4"
fi

echo ""
echo "  Next steps:"
echo "  1. Open in QuickTime to verify"
echo "  2. Upload to YouTube"
