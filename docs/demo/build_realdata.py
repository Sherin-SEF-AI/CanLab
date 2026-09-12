#!/usr/bin/env python3
"""Turn the real-data recording into one narrated, subtitled 1080p video.

Reuses everything in ``build.py`` (voice synthesis, subtitle timing, the ffmpeg
mux) and only changes where the frames come from and that the result is a
single file rather than four parts.

    python docs/demo/build_realdata.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build as demo                                        # noqa: E402

BUILD = HERE / "build-realdata"
OUT_DIR = HERE.parent                                       # docs/
SLUG = "canlab-realdata-validation"


def main() -> int:
    scenes_path = BUILD / "scenes.json"
    if not scenes_path.exists():
        raise SystemExit("run docs/demo/record_realdata.py first")
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is required")

    # build.py resolves frame and audio paths against its own BUILD.
    demo.BUILD = BUILD
    demo.FRAMES = BUILD / "frames"
    demo.OUT_DIR = OUT_DIR

    scenes = json.loads(scenes_path.read_text())
    print(f"building from {len(scenes)} scenes, "
          f"{sum(len(s['frames']) for s in scenes)} frames")

    asyncio.run(demo.synthesise(scenes))
    for scene in scenes:
        scene["duration"] = demo.duration(Path(scene["audio"]))

    video = OUT_DIR / f"{SLUG}.mp4"
    srt = OUT_DIR / f"{SLUG}.srt"
    cues = demo.build_cues(scenes)
    demo.write_srt(cues, srt)
    demo.write_ass(cues, BUILD / f"subs-{SLUG}.ass")
    demo.build_video(scenes, video, BUILD / f"subs-{SLUG}.ass")

    total = sum(s["duration"] for s in scenes)
    size = video.stat().st_size / 1e6
    print("\n" + "-" * 62)
    print(f"  {video.relative_to(OUT_DIR.parent)}  "
          f"{int(total // 60)}:{int(total % 60):02d}  {size:.1f} MB")
    print(f"  {srt.relative_to(OUT_DIR.parent)}  {len(cues)} subtitle cues")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
