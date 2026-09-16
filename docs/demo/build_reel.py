#!/usr/bin/env python3
"""Render and encode the 90 second reel.

Shot lengths are in beats, so the whole cut list is laid on the same clock as
the music in ``music_reel``. Nothing is nudged by hand afterwards: if a shot
should land differently, its length changes in beats and everything after it
moves with the music rather than away from it.

    python docs/demo/build_reel.py

Requires the recorders' stills (build-tour, build-stress, build-tigor), ffmpeg,
Pillow, NumPy and SciPy. There is no narration, so nothing is synthesised and
the build is offline.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import music_reel as M  # noqa: E402
import reel as R  # noqa: E402

BUILD = HERE / "build-reel"
FRAMES = BUILD / "frames"
OUT = HERE.parent / "canlab-reel.mp4"
SR = M.SR
FPS = R.FPS


def plan() -> tuple[list[dict], int]:
    """Every shot with its first frame and length, in frames."""
    shots = R.build_shots()
    plan, index = [], 0
    for shot in shots:
        frames = max(1, int(round(shot.beats * M.BEAT * FPS)))
        plan.append({"shot": shot, "first": index, "frames": frames})
        index += frames
    return plan, index


def _render(job: tuple) -> int:
    first, count, kind, payload = job
    import reel as R_
    rng = np.random.default_rng(first)
    for i in range(count):
        if kind == "intro":
            img = R_.render_intro_frame(i, count)
        elif kind == "outro":
            img = R_.render_outro_frame(i, count)
        else:
            img = R_.render_shot_frame(payload, i, count, rng)
        img.save(FRAMES / f"{first + i:05d}.jpg", quality=92)
    return count


def main() -> int:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is required")
    if not R.TOUR.is_dir():
        raise SystemExit(f"missing stills: {R.TOUR}. Run record_tour.py first.")
    for optional in (R.STRESS, R.TIGOR):
        if not optional.is_dir():
            print(f"  (no {optional.name}: those shots are dropped)", flush=True)

    shots, total = plan()
    seconds = total / FPS
    print(f"{len(shots)} shots, {total} frames, {seconds:.1f}s at {M.BPM:.0f} BPM")
    for item in shots:
        shot = item["shot"]
        label = shot.kind if shot.kind != "shot" else (shot.accent or shot.line[:34])
        print(f"  {item['first'] / FPS:5.1f}s  {item['frames']:4d}f  "
              f"{shot.into:<5} {label}", flush=True)

    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True)

    jobs = [(item["first"], item["frames"], item["shot"].kind, item["shot"])
            for item in shots]
    print("rendering…", flush=True)
    with ProcessPoolExecutor() as pool:
        done = sum(pool.map(_render, jobs))
    print(f"  {done} frames", flush=True)

    print("scoring…", flush=True)
    audio = M.score(seconds)
    wav = BUILD / "score.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR),
                    "-ac", "2", "-i", "-", str(wav)],
                   input=audio.astype(np.float32).tobytes(), check=True)

    print("encoding…", flush=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-framerate", str(FPS), "-i", str(FRAMES / "%05d.jpg"),
                    "-i", str(wav),
                    "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "slow",
                    "-crf", "20", "-c:a", "aac", "-b:a", "224k",
                    "-movflags", "+faststart", str(OUT)], check=True)
    size = OUT.stat().st_size / 1e6
    print(f"\n{OUT}  {seconds:.0f}s  {size:.1f} MB  {W_H()}")
    return 0


def W_H() -> str:
    return f"{R.W}x{R.H}"


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    raise SystemExit(main())
