"""A quiet ambient bed for the tour, generated rather than licensed.

Sine voices with slow attacks over a four-chord loop, a soft sub an octave
under the root, and a gentle stereo detune. It is synthesised here so the
video carries no third-party audio and nothing to attribute or clear.

The level is an envelope keyed to the video's structure: present in the intro
and outro, well under the narration everywhere else, and faded out at the end.
"""
from __future__ import annotations

import numpy as np

SR = 48_000

#: MIDI notes per chord: Am(add9), Fmaj7, C(add9), G6. Slow and unresolved.
CHORDS = [
    [45, 57, 64, 67, 71],
    [41, 53, 60, 64, 69],
    [48, 55, 62, 64, 67],
    [43, 55, 62, 64, 71],
]
CHORD_SECONDS = 7.0
OVERLAP = 2.5


def _hz(note: int) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


def _chord(notes: list[int], seconds: float, rng: np.random.Generator) -> np.ndarray:
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.zeros((n, 2))
    for i, note in enumerate(notes):
        f = _hz(note)
        level = 0.55 if i == 0 else 0.22
        for ch, cents in ((0, -4.0), (1, 4.0)):
            fd = f * 2 ** (cents / 1200)
            phase = rng.uniform(0, 2 * np.pi)
            wobble = 1 + 0.12 * np.sin(2 * np.pi * rng.uniform(0.05, 0.13) * t + phase)
            voice = np.sin(2 * np.pi * fd * t + phase)
            voice += 0.12 * np.sin(4 * np.pi * fd * t + phase)       # a little body
            out[:, ch] += level * wobble * voice
    # slow in, slow out, so chords breathe into each other
    env = np.minimum(1, t / OVERLAP) * np.minimum(1, (seconds - t) / OVERLAP)
    return out * env[:, None] ** 1.5


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    """Two gentle one-pole low-passes in series. Takes the edge off the upper voices."""
    from scipy.signal import lfilter
    a = np.exp(-2 * np.pi * cutoff / SR)
    for _ in range(2):
        x = lfilter([1 - a], [1, -a], x, axis=0)
    return x


def bed(seconds: float, seed: int = 7) -> np.ndarray:
    """Stereo float array, peak around 1.0, `seconds` long."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    out = np.zeros((n + SR * 10, 2))
    step = CHORD_SECONDS - OVERLAP
    pos, k = 0.0, 0
    while pos < seconds + 1:
        seg = _chord(CHORDS[k % len(CHORDS)], CHORD_SECONDS, rng)
        start = int(pos * SR)
        out[start:start + len(seg)] += seg
        pos += step
        k += 1
    out = out[:n]
    smooth = _lowpass(out, 1400.0)
    peak = np.max(np.abs(smooth)) or 1.0
    return smooth / peak


def envelope(seconds: float, keys: list[tuple[float, float]], ramp: float = 1.5) -> np.ndarray:
    """A gain curve through (time, level) keyframes with smooth ramps.

    Built at 100 points a second and interpolated up; smoothing at the audio
    rate is a convolution with a kernel tens of thousands of samples wide.
    """
    rate = 100
    coarse_t = np.arange(int(seconds * rate) + 1) / rate
    g = np.interp(coarse_t, [k[0] for k in keys], [k[1] for k in keys])
    width = max(3, int(ramp * rate))
    kernel = np.hanning(width)
    kernel /= kernel.sum()
    g = np.convolve(np.pad(g, width, mode="edge"), kernel, mode="same")[width:-width]
    return np.interp(np.arange(int(seconds * SR)) / SR, coarse_t, g)
