"""Render and encode the command line tour in HD.

    python docs/demo/fetch_cli_data.py      # the real recordings
    python docs/demo/record_cli.py          # run them, keep the output
    python docs/demo/build_cli_tour.py      # draw it and encode

The picture is driven entirely by the transcript, so re-recording on a faster
machine changes the video's pacing without anything here being edited. Music
is synthesised by ``music_reel`` at the length the cut turns out to be, and
the chapter crossfades are done on the rendered frames rather than in a filter
graph, so the same frames can be inspected as stills.

Needs ffmpeg, Pillow, NumPy and SciPy. Nothing is downloaded at build time.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cli_tour as C  # noqa: E402
import music_reel as M  # noqa: E402

BUILD = HERE / "build-cli"
FRAMES = BUILD / "frames"
TRANSCRIPT = BUILD / "transcript.json"
OUT = Path.home() / "Desktop" / "canlab-cli-tour.mp4"
FPS = C.FPS
SR = M.SR
XFADE = 14                      # frames of crossfade at a chapter change


def plan(shots) -> tuple[list[dict], int]:
    """Every segment with its first frame and length, in frames."""
    items: list[dict] = []
    index = 0

    intro = int(round(C.INTRO_S * FPS))
    items.append({"kind": "intro", "first": 0, "frames": intro})
    index += intro

    chapter = None
    chapter_start = 0
    for shot in shots:
        n = max(1, int(round(shot.duration * FPS)))
        first_of_chapter = shot.chapter != chapter
        if first_of_chapter:
            chapter = shot.chapter
            chapter_start = index
        items.append({"kind": "shot", "first": index, "frames": n, "shot": shot,
                      "first_of_chapter": first_of_chapter,
                      "chapter_offset": (index - chapter_start) / FPS})
        index += n

    outro = int(round(C.OUTRO_S * FPS))
    items.append({"kind": "outro", "first": index, "frames": outro})
    index += outro
    return items, index


def _render(job: tuple) -> int:
    kind, first, count, payload = job
    import cli_tour as C_
    for i in range(count):
        if kind == "intro":
            img = C_.render_intro_frame(i, count)
        elif kind == "outro":
            img = C_.render_outro_frame(i, count)
        else:
            shot, first_of_chapter, offset = payload
            img = C_.render_shot_frame(shot, i, count, first_of_chapter,
                                       offset + i / C_.FPS)
        img.save(FRAMES / f"{first + i:05d}.jpg", quality=93)
    return count


def crossfade(items: list[dict]) -> int:
    """Blend the boundary frames where a chapter changes, and at each end."""
    blended = 0
    boundaries = [it["first"] for it in items
                  if it["kind"] == "outro"
                  or (it["kind"] == "shot" and it.get("first_of_chapter"))]
    boundaries += [items[1]["first"]] if len(items) > 1 else []
    for start in sorted(set(boundaries)):
        before = FRAMES / f"{start - 1:05d}.jpg"
        if start == 0 or not before.is_file():
            continue
        tail = Image.open(before).convert("RGB")
        for k in range(XFADE):
            path = FRAMES / f"{start + k:05d}.jpg"
            if not path.is_file():
                break
            frame = Image.open(path).convert("RGB")
            a = (k + 1) / (XFADE + 1)
            Image.blend(tail, frame, a).save(path, quality=93)
            blended += 1
    return blended


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--preview", action="store_true",
                    help="render a few stills instead of the whole video")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is required")
    if not TRANSCRIPT.is_file():
        raise SystemExit(f"no transcript at {TRANSCRIPT}; run record_cli.py first")

    transcript = json.loads(TRANSCRIPT.read_text())
    shots = C.build_shots(transcript)
    items, total = plan(shots)
    seconds = total / FPS
    print(f"{len(shots)} commands, {total} frames, {seconds:.1f} s at {FPS} fps")
    chapter = None
    for it in items:
        if it["kind"] != "shot":
            print(f"  {it['first'] / FPS:6.1f}s  {it['frames']:4d}f  {it['kind']}")
            continue
        shot = it["shot"]
        if shot.chapter != chapter:
            chapter = shot.chapter
            print(f"  ── {C.CHAPTERS.get(chapter, chapter)}")
        print(f"  {it['first'] / FPS:6.1f}s  {it['frames']:4d}f  "
              f"{len(shot.lines):3d} lines  {shot.cmd[:64]}")

    if args.preview:
        BUILD.mkdir(parents=True, exist_ok=True)
        picks = [("intro", 150), ("shot", 0), ("shot", 1), ("shot", 4),
                 ("shot", 11), ("outro", 90)]
        shot_items = [it for it in items if it["kind"] == "shot"]
        for name, n in picks:
            if name == "intro":
                img = C.render_intro_frame(n, int(C.INTRO_S * FPS))
                out = BUILD / "preview-intro.jpg"
            elif name == "outro":
                img = C.render_outro_frame(n, int(C.OUTRO_S * FPS))
                out = BUILD / "preview-outro.jpg"
            else:
                it = shot_items[n]
                shot = it["shot"]
                frames = it["frames"]
                img = C.render_shot_frame(shot, int(frames * 0.82), frames,
                                          it["first_of_chapter"], 1.0)
                out = BUILD / f"preview-shot{n:02d}.jpg"
            img.save(out, quality=94)
            print(f"  wrote {out}")
        return 0

    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True)

    jobs = []
    for it in items:
        payload = ((it["shot"], it["first_of_chapter"], it["chapter_offset"])
                   if it["kind"] == "shot" else None)
        jobs.append((it["kind"], it["first"], it["frames"], payload))
    print("rendering…", flush=True)
    with ProcessPoolExecutor() as pool:
        done = sum(pool.map(_render, jobs))
    print(f"  {done} frames", flush=True)

    print("crossfading chapter changes…", flush=True)
    print(f"  {crossfade(items)} frames blended", flush=True)

    print("scoring…", flush=True)
    audio = M.score(seconds) * 0.62          # a bed, not the point
    wav = BUILD / "score.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR),
                    "-ac", "2", "-i", "-", str(wav)],
                   input=audio.astype(np.float32).tobytes(), check=True)

    print("encoding…", flush=True)
    out = Path(args.out)
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-framerate", str(FPS), "-i", str(FRAMES / "%05d.jpg"),
                    "-i", str(wav),
                    "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "slow",
                    "-crf", "19", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", "-shortest", str(out)], check=True)
    size = out.stat().st_size / 1e6
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,nb_frames,avg_frame_rate",
         "-of", "default=nw=1:nk=1", str(out)],
        capture_output=True, text=True).stdout.split()
    print(f"\n{out}")
    print(f"  {seconds:.0f} s   {size:.1f} MB   {'x'.join(probe[:2])}   "
          f"{probe[3] if len(probe) > 3 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
