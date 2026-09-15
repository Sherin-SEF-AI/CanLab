#!/usr/bin/env python3
"""Encode the guided tour: intro, the beats, outro, narration and a music bed.

For each beat the frame pushes in on the control being talked about, dims the
rest, rings it, captions it, holds, and pulls back out. The rectangle it pushes
in on is the widget's real geometry, captured at record time by
``record_tour.py``, so a control that moves in a later build takes its callout
with it.

Around that:

- an animated intro, in which the logo draws itself and the wordmark arrives
  (``titles.py``);
- a chapter title that slides in at the start of each section;
- crossfades between beats instead of hard cuts;
- an outro with where to get it, fading to black;
- a quiet ambient bed under the narration, generated in ``music.py`` so the
  video carries no third-party audio.

Order matters. The narration is synthesised and measured first, every beat's
length is set from its own speech, and only then are frames rendered. The
audio is then assembled on the same timeline, each clip placed at its beat's
start. An earlier version concatenated the clips end to end while each beat
ran 0.7 s longer than its speech, so the voice drifted ahead of the picture
by about 13 seconds over the video.

    python docs/demo/build_tour.py

Requires ffmpeg, ffprobe, Pillow, NumPy, SciPy and edge-tts. Run
record_tour.py first.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build import (  # noqa: E402
    FPS, duration, run, sentences, synthesise, wrap, write_srt, write_vtt,
)
from compose import Rect, frame_count, load_font, render_shot  # noqa: E402

BUILD = HERE / "build-tour"
STILLS = BUILD / "stills"
FRAMES = BUILD / "frames"
AUDIO = BUILD / "audio"
TITLE_AUDIO = BUILD / "audio-titles"
OUT_DIR = HERE.parent                      # docs/

VIDEO = OUT_DIR / "canlab-tour.mp4"
SUBS_SRT = OUT_DIR / "canlab-tour.srt"
SUBS_VTT = OUT_DIR / "canlab-tour.vtt"

#: x264 quality. Text stays legible at 26, and most of each spotlit frame is
#: deliberately blurred. 19 made a file twelve times the size of the others.
CRF = "26"

#: Caption size in the rendered frame, in points.
CAPTION_PT = 32

#: Speech starts this far into a beat, so the push-in is already moving, and
#: the beat runs this long after the speech ends, so the pull-out is not cut.
LEAD_IN = 0.35
TAIL = 0.6

#: Frames each side of a cut that are blended into a crossfade.
XFADE_HALF = 6

INTRO_TEXT = ("CanLab. A desktop workstation for reverse-engineering a CAN "
              "bus. Here is a tour of it, on real recordings.")
OUTRO_TEXT = ("CanLab is open source, under the MIT licence. The source and "
              "the Linux build are on GitHub, and the documentation is at the "
              "second link.")

SR = 48_000


# ── narration ────────────────────────────────────────────────────────────────

def decode(path: Path) -> np.ndarray:
    """An audio file as mono float32 at SR."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le",
         "-ac", "1", "-ar", str(SR), "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def plan(shots: list[dict]) -> list[dict]:
    """The whole programme as segments, each with a length and a voice offset."""
    import titles

    intro = {"key": "intro", "kind": "intro", "narration": INTRO_TEXT}
    outro = {"key": "outro", "kind": "outro", "narration": OUTRO_TEXT}
    asyncio.run(synthesise([intro, outro], audio_dir=TITLE_AUDIO))
    asyncio.run(synthesise(shots, audio_dir=AUDIO))

    segments = []
    speech = duration(intro["audio"])
    segments.append({**intro, "voice_at": titles.INTRO_VOICE_AT,
                     "speech": speech,
                     "duration": max(8.5, titles.INTRO_VOICE_AT + speech + 1.0)})
    for shot in shots:
        speech = duration(shot["audio"])
        segments.append({**shot, "kind": "beat", "voice_at": LEAD_IN,
                         "speech": speech,
                         "duration": LEAD_IN + speech + TAIL})
    speech = duration(outro["audio"])
    segments.append({**outro, "voice_at": titles.OUTRO_VOICE_AT,
                     "speech": speech,
                     "duration": max(9.5, titles.OUTRO_VOICE_AT + speech + 3.2)})

    start = 0
    for seg in segments:
        seg["frames"] = frame_count(seg["duration"])
        seg["first"] = start
        seg["start_s"] = start / FPS
        start += seg["frames"]
    return segments


# ── frames ───────────────────────────────────────────────────────────────────

def _render_title(job: tuple) -> int:
    kind, index, local, total_s = job
    import titles
    t = local / FPS
    img = (titles.intro_frame if kind == "intro" else titles.outro_frame)(t, total_s)
    img.save(FRAMES / f"{index:06d}.jpg", quality=92)
    return 1


def _render_beat(job: tuple) -> tuple[str, int]:
    key, still, first, seconds, focus, caption = job
    import titles

    def chip(frame, i):
        return titles.chip_overlay(frame, i / FPS, key)

    chip_frames = (int((titles.CHIP_OUT + 0.5) * FPS)
                   if key in titles.CHAPTERS else 0)
    written = render_shot(STILLS / still, FRAMES, first, seconds=seconds,
                          focus=Rect(**focus) if focus else None,
                          text=caption, font=load_font(CAPTION_PT),
                          overlay=chip, overlay_frames=chip_frames)
    return key, written


def render(segments: list[dict]) -> int:
    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True)

    title_jobs, beat_jobs = [], []
    for seg in segments:
        if seg["kind"] == "beat":
            beat_jobs.append((seg["key"], seg["still"], seg["first"],
                              seg["duration"], seg["focus"],
                              seg.get("caption", "")))
        else:
            title_jobs += [(seg["kind"], seg["first"] + i, i, seg["duration"])
                           for i in range(seg["frames"])]

    with ProcessPoolExecutor() as pool:
        done = sum(pool.map(_render_title, title_jobs, chunksize=8))
        print(f"  rendered intro and outro, {done} frames", flush=True)
        for key, written in pool.map(_render_beat, beat_jobs):
            print(f"  rendered {key:<11} {written:4d} frames", flush=True)
    return segments[-1]["first"] + segments[-1]["frames"]


def crossfade(segments: list[dict]) -> None:
    """Blend the frames either side of every cut into a dissolve.

    The frame count does not change, so the audio timeline stays put. Each cut
    lands on a fully zoomed-out frame on both sides, which is what makes a
    plain dissolve read as a camera move rather than a jump.
    """
    from PIL import Image

    half = XFADE_HALF
    for seg in segments[1:]:
        cut = seg["first"]
        paths = [FRAMES / f"{cut + k:06d}.jpg" for k in range(-half, half)]
        frames = [Image.open(p).convert("RGB") for p in paths]
        before, after = frames[half - 1], frames[half]
        for k, path in enumerate(paths):
            alpha = (k + 0.5) / (2 * half)
            a = frames[k] if k < half else before
            b = after if k < half else frames[k]
            mixed = Image.blend(a, b, alpha)
            os.unlink(path)            # hold frames are hard links; do not write through
            mixed.save(path, quality=92)


# ── audio ────────────────────────────────────────────────────────────────────

def mix(segments: list[dict], total_frames: int) -> Path:
    import music

    seconds = total_frames / FPS
    n = int(seconds * SR)
    voice = np.zeros(n, dtype=np.float32)
    for seg in segments:
        clip = decode(seg["audio"])
        at = int((seg["start_s"] + seg["voice_at"]) * SR)
        end = min(n, at + len(clip))
        voice[at:end] += clip[:end - at]
    peak = float(np.abs(voice).max()) or 1.0
    voice *= 0.9 / peak

    intro, outro = segments[0], segments[-1]
    intro_voice = intro["voice_at"]
    body_start = segments[1]["start_s"]
    outro_start = outro["start_s"]
    outro_voice_end = outro_start + outro["voice_at"] + outro["speech"]
    keys = [
        (0.0, 0.0), (0.8, 0.42), (intro_voice - 0.3, 0.42),
        (intro_voice + 0.4, 0.20), (body_start + 0.5, 0.075),
        (outro_start - 0.5, 0.075), (outro_start + 0.6, 0.20),
        (outro_voice_end + 0.3, 0.42), (seconds - 1.6, 0.30), (seconds, 0.0),
    ]
    bed = music.bed(seconds) * music.envelope(seconds, keys)[:, None]

    stereo = bed[:n] + voice[:, None]
    peak = float(np.abs(stereo).max())
    if peak > 0.98:
        stereo *= 0.98 / peak
    wav = BUILD / "mix.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR),
         "-ac", "2", "-i", "-", str(wav)],
        input=stereo.astype(np.float32).tobytes(), check=True)
    return wav


def cues(segments: list[dict]) -> list[tuple[float, float, str]]:
    """Subtitle cues timed to where each clip actually sits on the timeline."""
    out = []
    for seg in segments:
        parts = sentences(seg["narration"])
        chars = sum(len(p) for p in parts) or 1
        start = seg["start_s"] + seg["voice_at"]
        for part in parts:
            span = seg["speech"] * len(part) / chars
            out.append((start, start + span, wrap(part)))
            start += span
    return out


def encode(wav: Path) -> None:
    silent = BUILD / "silent.mp4"
    print("  encoding video…", flush=True)
    run(["ffmpeg", "-y", "-v", "error",
         "-framerate", str(FPS), "-i", str(FRAMES / "%06d.jpg"),
         "-vf", "format=yuv420p", "-c:v", "libx264",
         "-preset", "slow", "-crf", CRF, str(silent)])
    # The narration is a switchable subtitle track rather than burnt in: the
    # picture already carries a callout caption on every beat.
    print("  muxing audio and subtitles…", flush=True)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(silent), "-i", str(wav),
         "-i", str(SUBS_SRT),
         "-map", "0:v", "-map", "1:a", "-map", "2:s",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
         "-movflags", "+faststart", str(VIDEO)])


def main() -> int:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is required")
    manifest = BUILD / "shots.json"
    if not manifest.is_file():
        raise SystemExit(f"run record_tour.py first: no {manifest}")
    shots = json.loads(manifest.read_text())

    print(f"narrating {len(shots)} beats plus intro and outro…", flush=True)
    segments = plan(shots)
    print(f"  {segments[-1]['start_s'] + segments[-1]['duration']:.0f}s programme",
          flush=True)

    print("rendering frames…", flush=True)
    total = render(segments)
    print("  crossfading cuts…", flush=True)
    crossfade(segments)

    print("mixing audio…", flush=True)
    wav = mix(segments, total)
    subtitle = cues(segments)
    write_srt(subtitle, SUBS_SRT)
    write_vtt(subtitle, SUBS_VTT)

    print("encoding…", flush=True)
    encode(wav)

    size_mb = VIDEO.stat().st_size / 1e6
    length = duration(VIDEO)
    pushes = sum(1 for s in segments if s.get("focus"))
    print(f"\n{VIDEO.relative_to(OUT_DIR.parent)}  "
          f"{int(length // 60)}m{int(length % 60):02d}s  {size_mb:.1f} MB  "
          f"{total} frames, intro, {len(shots)} beats ({pushes} push in), outro")
    print(f"{SUBS_SRT.relative_to(OUT_DIR.parent)} and .vtt  {len(subtitle)} cues")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
