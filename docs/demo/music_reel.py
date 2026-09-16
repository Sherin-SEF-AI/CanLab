"""A tempo-locked score for the 90 second reel, synthesised here.

The tour's bed is a pad: slow, unmeasured, meant to sit under a voice. A reel
is cut to music, so the music has to have a grid to cut to. This one is built
from a beat clock at a fixed tempo, so the same numbers that place a kick also
place a cut, and the picture lands with the sound rather than near it.

Everything is generated: sub kick, clap, hats, a bass that follows the chords,
a plucked arpeggio, a pad, plus risers and impacts at the section changes.
Nothing is sampled, so there is nothing to licence and nothing to attribute.

Two things make it sound produced rather than assembled. The pad and bass are
ducked on every kick, which is the pumping that most modern music has and its
absence is what makes a homemade track sound flat. And a feedback delay with a
darkening filter stands in for a room, so the plucks have somewhere to go.
"""
from __future__ import annotations

import numpy as np

SR = 48_000
BPM = 100.0
BEAT = 60.0 / BPM               # 0.6 s
BAR = 4 * BEAT                  # 2.4 s

#: i - VI - III - VII in A minor: the progression that sounds like resolve
#: without ever arriving. One chord per bar.
CHORDS = [
    [57, 60, 64, 69],           # Am
    [53, 57, 60, 65],           # F
    [55, 60, 64, 67],           # C
    [55, 59, 62, 67],           # G
]
ROOTS = [33, 29, 31, 31]        # bass notes, two octaves down


def hz(note: float) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


def _env(n: int, attack: float, decay: float, power: float = 1.0) -> np.ndarray:
    """A percussive envelope: near-instant attack, exponential fall."""
    t = np.arange(n) / SR
    rise = np.minimum(1.0, t / max(1e-5, attack))
    fall = np.exp(-t / max(1e-5, decay))
    return (rise * fall) ** power


def kick(strength: float = 1.0) -> np.ndarray:
    """Sine with a pitch drop: the shape of every electronic kick."""
    n = int(0.42 * SR)
    t = np.arange(n) / SR
    freq = 120 * np.exp(-t / 0.035) + 45
    phase = 2 * np.pi * np.cumsum(freq) / SR
    body = np.sin(phase) * _env(n, 0.001, 0.13)
    click = np.random.default_rng(1).normal(0, 1, n) * _env(n, 0.0005, 0.006) * 0.25
    return (body + click) * strength


def clap(rng: np.random.Generator) -> np.ndarray:
    """Three short bursts a few milliseconds apart, then a tail."""
    n = int(0.3 * SR)
    out = np.zeros(n)
    for offset, level in ((0, 0.7), (0.011, 0.85), (0.023, 1.0)):
        start = int(offset * SR)
        burst = rng.normal(0, 1, n - start) * _env(n - start, 0.0005, 0.012)
        out[start:] += burst * level
    out += rng.normal(0, 1, n) * _env(n, 0.02, 0.09) * 0.35
    return _highpass(out, 900) * 0.5


def hat(rng: np.random.Generator, open_: bool = False) -> np.ndarray:
    n = int((0.16 if open_ else 0.05) * SR)
    noise = rng.normal(0, 1, n)
    return _highpass(noise * _env(n, 0.0004, 0.045 if open_ else 0.012), 7500) * 0.1


def pluck(note: float, beats: float, rng: np.random.Generator) -> np.ndarray:
    """Karplus-Strong: a plucked string from noise and a delay line."""
    n = int(beats * BEAT * SR)
    period = max(2, int(SR / hz(note)))
    buf = rng.normal(0, 1, period)
    out = np.empty(n)
    damp = 0.492
    for i in range(n):
        out[i] = buf[i % period]
        nxt = (i + 1) % period
        buf[i % period] = damp * (buf[i % period] + buf[nxt])
    voice = out * _env(n, 0.002, beats * BEAT * 0.55)
    # The pluck carries the tune, and the tune lives in the mids. Without
    # this the mix was 3% between 500 and 2000 Hz: all weight and air, no
    # melody, which is the sound of a track assembled rather than mixed.
    # Karplus-Strong is bright by nature, so the lift is band limited: the
    # melody comes forward without the string turning into a hi-hat.
    lifted = voice + 0.5 * _highpass(voice, 700)
    return _lowpass(lifted, 3200) * 0.7


def bass(note: float, beats: float) -> np.ndarray:
    n = int(beats * BEAT * SR)
    t = np.arange(n) / SR
    f = hz(note)
    wave = (np.sin(2 * np.pi * f * t)
            + 0.35 * np.sin(4 * np.pi * f * t)
            + 0.18 * np.sin(6 * np.pi * f * t))
    return _lowpass(wave, 420) * _env(n, 0.006, beats * BEAT * 0.8) * 0.5


def pad(notes: list[int], beats: float, rng: np.random.Generator) -> np.ndarray:
    n = int(beats * BEAT * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for note in notes:
        for detune in (-5, 5):
            f = hz(note) * 2 ** (detune / 1200)
            out += np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28))
    shape = np.minimum(1, t / 0.4) * np.minimum(1, (beats * BEAT - t) / 0.5)
    return _lowpass(out / (2 * len(notes)), 2600) * shape * 0.26


def riser(beats: float, rng: np.random.Generator) -> np.ndarray:
    """Noise sweeping up into a section change."""
    n = int(beats * BEAT * SR)
    t = np.linspace(0, 1, n)
    noise = rng.normal(0, 1, n)
    swept = _highpass(noise, 300) * (t ** 2)
    tone = np.sin(2 * np.pi * np.cumsum(200 + 2400 * t ** 2) / SR) * (t ** 3) * 0.3
    return (swept * 0.25 + tone) * 0.5


def impact(rng: np.random.Generator) -> np.ndarray:
    """A low boom for the first frame of a section."""
    n = int(1.2 * SR)
    t = np.arange(n) / SR
    freq = 90 * np.exp(-t / 0.25) + 32
    boom = np.sin(2 * np.pi * np.cumsum(freq) / SR) * _env(n, 0.002, 0.42)
    air = _highpass(rng.normal(0, 1, n), 2000) * _env(n, 0.001, 0.25) * 0.25
    return (boom + air) * 0.8


# ── filters and space ────────────────────────────────────────────────────────

def _lowpass(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    from scipy.signal import lfilter
    a = np.exp(-2 * np.pi * cutoff / SR)
    for _ in range(order):
        x = lfilter([1 - a], [1, -a], x, axis=0)
    return x


def _highpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    return x - _lowpass(x, cutoff, order=1)


def _delay(x: np.ndarray, beats: float = 0.75, feedback: float = 0.34,
           mix: float = 0.22) -> np.ndarray:
    """A dotted-eighth feedback delay, darkened each pass."""
    step = int(beats * BEAT * SR)
    out = x.copy()
    tail = x.copy()
    for _ in range(4):
        tail = _lowpass(np.concatenate([np.zeros(step), tail[:-step]]), 1800) * feedback
        out += tail * mix
    return out


# ── the arrangement ──────────────────────────────────────────────────────────

def score(seconds: float, seed: int = 5) -> np.ndarray:
    """Stereo float array for the reel, arranged in four-bar sections.

    The arrangement is deliberately simple: two bars of pad and riser, then
    drums, then everything, then a break where the picture makes its point,
    then the last section and a tail. The reel's cuts use the same clock.
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * SR) + SR
    left = np.zeros(n)
    right = np.zeros(n)
    kicks: list[int] = []

    def place(buf_l, buf_r, sample, at, gain=1.0, pan=0.0):
        start = int(at * SR)
        end = min(len(buf_l), start + len(sample))
        if end <= start:
            return
        piece = sample[:end - start] * gain
        buf_l[start:end] += piece * (1 - max(0.0, pan))
        buf_r[start:end] += piece * (1 + min(0.0, pan))

    bars = int(seconds / BAR) + 1
    for bar in range(bars):
        t0 = bar * BAR
        chord = CHORDS[bar % 4]
        root = ROOTS[bar % 4]
        section = "intro" if bar < 2 else "build" if bar < 4 else \
                  "break" if 16 <= bar < 18 else "main"

        # pad, every bar
        place(left, right, pad(chord, 4, rng), t0, 0.9, -0.15)
        place(left, right, pad(chord, 4, rng), t0, 0.9, 0.15)

        if section in ("build", "main"):
            for beat in (0, 2):                      # four to the floor, halved
                place(left, right, kick(1.0), t0 + beat * BEAT, 1.0)
                kicks.append(int((t0 + beat * BEAT) * SR))
            if section == "main":
                for beat in (1, 3):
                    place(left, right, kick(0.8), t0 + beat * BEAT, 0.8)
                    kicks.append(int((t0 + beat * BEAT) * SR))
                place(left, right, clap(rng), t0 + BEAT, 0.9, 0.1)
                place(left, right, clap(rng), t0 + 3 * BEAT, 0.9, -0.1)
            for eighth in range(8):
                if eighth % 2 or section == "main":
                    place(left, right, hat(rng, open_=(eighth == 6)),
                          t0 + eighth * BEAT / 2, 0.8, 0.25 if eighth % 2 else -0.25)
            place(left, right, bass(root, 4), t0, 1.0)

        if section in ("main", "break"):
            notes = chord + [chord[0] + 12]
            for i, beat in enumerate((0, 0.75, 1.5, 2.25, 3.0, 3.75)):
                note = notes[(bar + i) % len(notes)] + (12 if i % 2 else 0)
                place(left, right, pluck(note, 0.8, rng), t0 + beat * BEAT,
                      0.55 if section == "main" else 0.75,
                      0.4 * (1 if i % 2 else -1))

        if bar in (3, 15, 17):                        # into each change
            place(left, right, riser(4, rng), t0, 0.8)
        if bar in (4, 18):
            place(left, right, impact(rng), t0, 1.0)

    # space, then the pumping that ties it together
    left = _delay(left)
    right = _delay(right, beats=0.5)
    duck = _sidechain(n, kicks)
    left *= duck
    right *= duck

    stereo = np.stack([left, right], axis=1)[:int(seconds * SR)]
    return _master(stereo)


def _sidechain(n: int, kicks: list[int], depth: float = 0.55,
               release: float = 0.28) -> np.ndarray:
    """Duck everything on each kick, and let it breathe back in."""
    gain = np.ones(n)
    shape_len = int(release * SR)
    shape = 1 - depth * np.exp(-np.arange(shape_len) / (0.09 * SR))
    for at in kicks:
        end = min(n, at + shape_len)
        if end > at:
            gain[at:end] = np.minimum(gain[at:end], shape[:end - at])
    return gain


def _master(stereo: np.ndarray) -> np.ndarray:
    """Gentle compression into a hard ceiling, so it is loud but not clipped."""
    x = stereo / (np.max(np.abs(stereo)) or 1.0)
    # soft knee: tanh rounds the peaks rather than flattening them
    # Saturation generates harmonics, so a heavy drive brightens the whole
    # mix as a side effect. Enough to round the peaks, not enough to re-EQ it.
    x = np.tanh(x * 1.1) / np.tanh(1.1)
    x = _lowpass(x, 9000, order=1)                     # fizz off the hats
    x = x + 0.20 * _highpass(_lowpass(x, 2500), 600)   # lift the melody band
    x = x + 0.55 * _lowpass(x, 140)                    # weight back under it
    x = x - x.mean(axis=0)
    peak = np.max(np.abs(x)) or 1.0
    return x * (0.94 / peak)


def beats(seconds: float) -> np.ndarray:
    """Every beat time in the piece, for cutting the picture to it."""
    return np.arange(0, seconds, BEAT)
