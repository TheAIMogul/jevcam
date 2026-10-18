#!/usr/bin/env bash
# Turn the Grok card clips (card_*.mp4 from gen_video.py) into seamless loops for the chooser.
# The last second crossfades into the clip's second 1, and the loop starts there, so the
# wrap-around has no jump. Output: ../assets/<name>.mp4, H.264, no audio, small enough for the Pi.
set -euo pipefail
cd "$(dirname "$0")"
FADE=1
for src in card_*.mp4; do
  name="${src%.mp4}"
  dur=$(ffprobe -v error -select_streams v:0 -show_entries stream=duration -of csv=p=0 "$src")
  offset=$(python3 -c "print(round($dur - 2 * $FADE, 3))")
  ffmpeg -v error -y -i "$src" -filter_complex \
    "[0:v]split[a][b];[a]trim=start=$FADE,setpts=PTS-STARTPTS[body];[b]trim=0:$FADE,setpts=PTS-STARTPTS[head];\
[body][head]xfade=transition=fade:duration=$FADE:offset=$offset,format=yuv420p[v]" \
    -map "[v]" -an -c:v libx264 -preset slow -crf 26 -profile:v high -movflags +faststart \
    "../assets/$name.mp4"
  echo "$name.mp4 $(( $(stat -f%z "../assets/$name.mp4") / 1024 )) KB"
done
