#!/usr/bin/env sh
set -e
URL="$1"
DEST="$2"
MIN_BYTES="${3:-1024}"

if [ -z "$URL" ] || [ -z "$DEST" ]; then
    echo "Usage: download_model.sh <url> <dest_path> [min_bytes]" >&2
    exit 2
fi

mkdir -p "$(dirname "$DEST")"

for attempt in 1 2 3; do
    echo "[download_model] attempt $attempt/3: $URL -> $DEST"
    if wget --tries=1 --timeout=60 --quiet -O "$DEST" "$URL"; then
        actual=$(wc -c < "$DEST" 2>/dev/null || echo 0)
        if [ "$actual" -ge "$MIN_BYTES" ]; then
            echo "[download_model] OK: $DEST ($actual bytes)"
            exit 0
        fi
        echo "[download_model] WARN attempt $attempt: $DEST is $actual bytes, need >= $MIN_BYTES" >&2
        rm -f "$DEST"
    else
        echo "[download_model] WARN attempt $attempt: wget failed for $URL" >&2
    fi
    sleep 3
done

echo "[download_model] FAIL: 3 attempts exhausted for $URL" >&2
exit 1
