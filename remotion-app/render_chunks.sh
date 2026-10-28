#!/bin/bash
# render_chunks.sh — Chunked Remotion render for long videos.
# Usage: ./render_chunks.sh <composition-id> <total-frames> [output-name]
#
# Renders 30-second chunks (900 frames) to avoid Chrome-headless crashes.
# Concatenates with ffmpeg at the end.

set -e

COMPOSITION="${1:?Usage: $0 <composition-id> <total-frames> [output-name]}"
TOTAL="${2:?Provide total frames}"
OUTPUT="${3:-${COMPOSITION}_final.mp4}"
CHUNK_SIZE=900
TIMEOUT=60000
CONCURRENCY=2

cd /Users/isamumatsuyama/Documents/online-projects/red-sun-japan/channel-redsunjapan/render-remotion

echo "=== Chunked render: $COMPOSITION ==="
echo "  Total frames: $TOTAL"
echo "  Chunk size: $CHUNK_SIZE frames (30s)"
echo "  Output: $OUTPUT"
echo ""

# Calculate chunks
CHUNKS=$(( (TOTAL + CHUNK_SIZE - 1) / CHUNK_SIZE ))
echo "  Chunks: $CHUNKS"
echo ""

# Render each chunk
CONCAT_LIST=".concat_render.txt"
> "$CONCAT_LIST"

for i in $(seq 0 $((CHUNKS - 1))); do
    START=$((i * CHUNK_SIZE))
    END=$((START + CHUNK_SIZE - 1))
    if [ "$END" -ge "$TOTAL" ]; then
        END=$((TOTAL - 1))
    fi
    CHUNK_FILE="chunk_${START}.mp4"
    
    # Skip if already exists
    if [ -f "$CHUNK_FILE" ]; then
        echo "  [$((i+1))/$CHUNKS] Skipping $CHUNK_FILE (exists)"
        echo "file '$CHUNK_FILE'" >> "$CONCAT_LIST"
        continue
    fi
    
    echo "  [$((i+1))/$CHUNKS] Rendering frames $START-$END..."
    npx remotion render "$COMPOSITION" "$CHUNK_FILE" \
        --frames=${START}-${END} \
        --concurrency=$CONCURRENCY \
        --timeout=$TIMEOUT 2>&1 | tail -3
    
    echo "file '$CHUNK_FILE'" >> "$CONCAT_LIST"
done

echo ""
echo "=== Concatenating $CHUNKS chunks ==="
ffmpeg -y -f concat -safe 0 -i "$CONCAT_LIST" \
    -c copy -avoid_negative_ts make_zero \
    -movflags +faststart \
    "$OUTPUT"

# Cleanup concat list
rm "$CONCAT_LIST"

echo ""
echo "✅ Done! Output: $OUTPUT"
ls -lh "$OUTPUT"
echo ""
echo "Duration:"
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$OUTPUT"
