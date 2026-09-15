#!/usr/bin/env python3
"""Encode the guided tour: narration first, then frames rendered to fit it.

The other two builders play a still for as long as its narration lasts, which
is a slideshow. This one renders every frame: for each beat it pushes in on the
control being talked about, dims the rest, rings it, captions it, holds, and
pulls back out. The rectangle it pushes in on is the widget's real geometry,
captured at record time, so a control that moves in a later build takes its
callout with it.

Order matters here. The narration is synthesised first and measured, and the
frames are then generated to that exact length, so a beat is never cut off
mid-sentence and never sits still waiting for one to finish.

    python docs/demo/build_tour.py

Requires ffmpeg, ffprobe, Pillow and edge-tts. Run record_tour.py first.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build import (  # noqa: E402
    FPS, build_cues, duration, run, synthesise, write_srt,
)
from compose import Rect, frame_count, load_font, render_shot  # noqa: E402

BUILD = HERE / "build-tour"
STILLS = BUILD / "stills"
FRAMES = BUILD / "frames"
AUDIO = BUILD / "audio"
OUT_DIR = HERE.parent                      # docs/

VIDEO = OUT_DIR / "canlab-tour.mp4"
SUBS_SRT = OUT_DIR / "canlab-tour.srt"
SUBS_VTT = OUT_DIR / "canlab-tour.vtt"

#: Caption size in the rendered frame, in points. Larger than the subtitle
#: font because it is part of the picture rather than an overlay on it.
CAPTION_PT = 32

#: x264 quality. 19 made a 96 MB file for five and a half minutes, near
#: GitHub's 100 MB limit and twelve times the other walkthrough parts. Text
#: stays legible at 26, and most of the spotlit area is deliberately blurred.
CRF = "26"

#: A beat runs for as long as its narration, plus a breath at each end so the
#: push-in has started before the first word and the pull-out is not clipped.
PAD_SECONDS = 0.7


def _render_one(job: tuple) -> tuple[str, int]:
    key, still, index, seconds, focus, caption = job
    written = render_shot(STILLS / still, FRAMES, index, seconds=seconds,
                          focus=Rect(**focus) if focus else None,
                          text=caption, font=load_font(CAPTION_PT))
    return key, written


def render_frames(shots: list[dict]) -> int:
    """Render every beat, in parallel. Returns the total frames written.

    Each beat's frame count depends only on its duration, so every beat's
    starting index is known before any of them renders and they can run in
    separate processes without coordinating.
    """
    from concurrent.futures import ProcessPoolExecutor

    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True)

    jobs, index = [], 0
    for shot in shots:
        jobs.append((shot["key"], shot["still"], index, shot["duration"],
                     shot["focus"], shot.get("caption", "")))
        shot["frames"] = frame_count(shot["duration"])
        index += shot["frames"]

    with ProcessPoolExecutor() as pool:
        for key, written in pool.map(_render_one, jobs):
            print(f"  rendered {key:<11} {written:4d} frames", flush=True)
    return index


def encode(total_frames: int) -> None:
    """Frames to video, narration to one track, then mux with soft subtitles.

    Every beat was rendered at exactly FPS, so the frames go in as a constant
    rate sequence rather than through a concat file with per-frame durations.
    """
    silent = BUILD / "silent.mp4"
    print(f"  encoding {total_frames} frames…")
    run(["ffmpeg", "-y", "-v", "error",
         "-framerate", str(FPS), "-i", str(FRAMES / "%06d.jpg"),
         "-vf", "format=yuv420p", "-c:v", "libx264",
         "-preset", "slow", "-crf", CRF, str(silent)])

    track = BUILD / "narration.mp3"
    listing = BUILD / "audio.txt"
    listing.write_text("\n".join(f"file '{s['audio']}'" for s in SHOTS))
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", str(track)])

    # The narration goes in as a subtitle track the player can switch, not
    # burnt into the picture. Each beat already has a callout caption drawn in
    # the frame; burning the narration in as well put two blocks of text on
    # screen at once, and on the site the <track> made it three.
    print("  muxing narration and a switchable subtitle track…")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(silent), "-i", str(track),
         "-i", str(SUBS_SRT),
         "-map", "0:v", "-map", "1:a", "-map", "2:s",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
         "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
         "-movflags", "+faststart", str(VIDEO)])


def write_vtt(cues, path: Path) -> None:
    """WebVTT, for the <track> element on the documentation site.

    The burnt-in captions are for the file on its own; a browser playing the
    video in the page wants a real text track it can turn off, index and read
    aloud. WebVTT is SRT with a header, no cue numbers and a full stop where
    SRT puts a comma.
    """
    from build import timestamp
    lines = ["WEBVTT", ""]
    for start, end, text in cues:
        lines.append(f"{timestamp(start).replace(',', '.')} --> "
                     f"{timestamp(end).replace(',', '.')}")
        lines += [text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


SHOTS: list[dict] = []


def main() -> int:
    global SHOTS
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is required")
    manifest = BUILD / "shots.json"
    if not manifest.is_file():
        raise SystemExit(f"run record_tour.py first: no {manifest}")
    SHOTS = json.loads(manifest.read_text())

    print(f"narrating {len(SHOTS)} beats…")
    asyncio.run(synthesise(SHOTS, audio_dir=AUDIO))

    # Frames are generated to fit the audio, never the other way round.
    for shot in SHOTS:
        spoken = duration(shot["audio"])
        shot["duration"] = spoken + PAD_SECONDS
    spoken_total = sum(s["duration"] for s in SHOTS)
    print(f"  {spoken_total:.0f}s of narration")

    print("rendering frames…")
    total = render_frames(SHOTS)

    cues = build_cues(SHOTS)
    write_srt(cues, SUBS_SRT)
    write_vtt(cues, SUBS_VTT)

    print("encoding…")
    encode(total)

    size_mb = VIDEO.stat().st_size / 1e6
    length = duration(VIDEO)
    pushes = sum(1 for s in SHOTS if s["focus"])
    print(f"\n{VIDEO.relative_to(OUT_DIR.parent)}  "
          f"{int(length // 60)}m{int(length % 60):02d}s  {size_mb:.1f} MB  "
          f"{total} frames, {pushes} of {len(SHOTS)} beats push in")
    print(f"{SUBS_SRT.relative_to(OUT_DIR.parent)} and .vtt  {len(cues)} cues")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
