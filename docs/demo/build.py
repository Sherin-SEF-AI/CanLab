#!/usr/bin/env python3
"""Turn the recorded frames into a narrated, subtitled 1080p video.

Reads ``build/scenes.json`` from ``record.py``, synthesises narration for each
scene, times the scene's frames to that audio, writes an SRT whose cues follow
the sentences, and muxes it all with ffmpeg.

    python docs/demo/build.py
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
FRAMES = BUILD / "frames"
AUDIO = BUILD / "audio"
OUT_DIR = HERE.parent                      # docs/
VIDEO = OUT_DIR / "canlab-demo.mp4"
SRT = OUT_DIR / "canlab-demo.srt"

VOICE = "en-GB-RyanNeural"
RATE = "-4%"                               # a little slower than default
FPS = 30
# Subtitle styling. The burn-in goes through ASS rather than the SRT because
# libass sizes fonts against the script's PlayRes, and an SRT has none -- it
# falls back to 288 lines, so a "27pt" font lands at about 100px on a 1080p
# frame. (ffmpeg's subtitles=original_size= is meant to fix that and does not.)
# An ASS header states PlayResX/Y explicitly, so FontSize is in real pixels.
FONT_SIZE = 40
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, \
BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, \
BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{size},&H00FFFFFF,&H000000FF,&H00000000,&HB4000000,\
0,0,0,0,100,100,0,0,4,1,0,2,80,80,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if result.returncode != 0:
        sys.stderr.write(result.stderr[-4000:])
        raise SystemExit(f"command failed: {' '.join(cmd[:4])}…")
    return result


def duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "csv=p=0", str(path)]).stdout.strip()
    return float(out)


async def synthesise(scenes: list[dict]) -> None:
    """One narration file per scene."""
    import edge_tts
    AUDIO.mkdir(parents=True, exist_ok=True)
    for i, scene in enumerate(scenes):
        path = AUDIO / f"{i:02d}_{scene['key']}.mp3"
        communicate = edge_tts.Communicate(scene["narration"], VOICE, rate=RATE)
        await communicate.save(str(path))
        scene["audio"] = path
        print(f"  voiced {scene['key']}")


def sentences(text: str) -> list[str]:
    """Split narration into subtitle-sized cues."""
    parts = re.split(r"(?<=[.:;])\s+", text.strip())
    cues: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Long sentences get split at a comma near the middle.
        while len(part) > 96:
            pivot = part.rfind(", ", 40, 100)
            if pivot == -1:
                pivot = part.rfind(" ", 40, 96)
            if pivot == -1:
                break
            cues.append(part[:pivot + 1].strip())
            part = part[pivot + 1:].strip()
        cues.append(part)
    return cues


def wrap(text: str, width: int = 62) -> str:
    """Wrap a cue to at most two lines."""
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width and line:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    if len(lines) > 2:                       # rebalance into two lines
        mid = len(text) // 2
        cut = text.rfind(" ", 0, mid + 12)
        lines = [text[:cut].strip(), text[cut:].strip()]
    return "\n".join(lines)


def timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ass_time(seconds: float) -> str:
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def build_cues(scenes: list[dict]) -> list[tuple[float, float, str]]:
    """Sentence-sized cues, timed proportionally inside each scene.

    The narration is one audio file per scene, so a cue's share of that
    scene's duration is its share of the scene's characters -- close enough
    with a constant speaking rate, and it never drifts across scenes.
    """
    cues, clock = [], 0.0
    for scene in scenes:
        parts = sentences(scene["narration"])
        total_chars = sum(len(c) for c in parts) or 1
        start = clock
        for part in parts:
            span = scene["duration"] * len(part) / total_chars
            cues.append((start, start + span, wrap(part)))
            start += span
        clock += scene["duration"]
    return cues


def write_srt(cues, path: Path) -> None:
    lines = []
    for i, (start, end, text) in enumerate(cues, 1):
        lines += [str(i), f"{timestamp(start)} --> {timestamp(end)}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_ass(cues, path: Path) -> None:
    body = []
    for start, end, text in cues:
        # "{" and "}" open an ASS override block; the renderer eats them.
        safe = text.replace("{", "(").replace("}", ")").replace("\n", "\\N")
        body.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},"
                    f"Default,,0,0,0,,{safe}")
    path.write_text(ASS_HEADER.format(size=FONT_SIZE) + "\n".join(body) + "\n",
                    encoding="utf-8")


def build_video(scenes: list[dict]) -> None:
    """Hold each scene's frames for its narration, then mux audio and subtitles."""
    concat = BUILD / "frames.txt"
    lines = []
    for scene in scenes:
        frames = scene["frames"]
        per_frame = scene["duration"] / max(len(frames), 1)
        for name in frames:
            lines.append(f"file '{FRAMES / name}'")
            lines.append(f"duration {per_frame:.4f}")
    lines.append(f"file '{FRAMES / scenes[-1]['frames'][-1]}'")   # concat quirk
    concat.write_text("\n".join(lines))

    silent = BUILD / "silent.mp4"
    print("  encoding video…")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(concat), "-vf", f"fps={FPS},format=yuv420p",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", str(silent)])

    track = BUILD / "narration.mp3"
    audio_list = BUILD / "audio.txt"
    audio_list.write_text(
        "\n".join(f"file '{s['audio']}'" for s in scenes))
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(audio_list), "-c", "copy", str(track)])

    print("  burning subtitles and muxing audio…")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(silent), "-i", str(track),
         "-vf", f"ass={BUILD / 'subs.ass'}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-shortest",
         "-movflags", "+faststart", str(VIDEO)])


def main() -> int:
    if not (BUILD / "scenes.json").exists():
        raise SystemExit("run docs/demo/record.py first")
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is required")

    scenes = json.loads((BUILD / "scenes.json").read_text())
    print(f"building demo from {len(scenes)} scenes")

    asyncio.run(synthesise(scenes))
    for scene in scenes:
        scene["duration"] = duration(Path(scene["audio"]))

    cues = build_cues(scenes)
    write_srt(cues, SRT)
    write_ass(cues, BUILD / "subs.ass")
    print(f"  {len(cues)} subtitle cues")
    build_video(scenes)

    total = sum(s["duration"] for s in scenes)
    size_mb = VIDEO.stat().st_size / 1e6
    print(f"\n{VIDEO}  ({total / 60:.1f} min, {size_mb:.1f} MB)")
    print(f"{SRT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
