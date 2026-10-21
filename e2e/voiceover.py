#!/usr/bin/env python3
"""Lay a spoken voiceover over the /demo recording, scene by scene, with subtitles.

Record the narration from demo/DEMO_SCRIPT.md in one take: one paragraph per scene, ~2 s pause between
paragraphs. Then:

  cd e2e
  VO="uv run --quiet --no-project --with numpy --with imageio-ffmpeg python voiceover.py"
  $VO plan --audio ~/memo.m4a                         # finds the 10 paragraphs, prints per-scene dwell times
  DEMO_DWELL="<printed by plan>" ./record-demo.sh     # re-records the tour so each scene fits its paragraph
  $VO mix --audio ~/memo.m4a [--words words.json]     # narration + .srt + burned-in captions

`mix` writes, next to demo/anti-radar-demo.mp4:
  anti-radar-demo-voiceover.mp4            video + narration (subtitles as a selectable track)
  anti-radar-demo-voiceover-captions.mp4   the same with captions burned in (for feeds that autoplay muted)
  anti-radar-demo-voiceover.srt            captions, for YouTube / LinkedIn / Drive uploads

--words: optional word timestamps ([{"start","end","words":[{"w","s","e"}]}], e.g. from faster-whisper) used
to time the captions; without it captions are spread evenly over each paragraph. Caption text always comes
from DEMO_SCRIPT.md (speech recognition misspells names).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPT = ROOT / "demo" / "DEMO_SCRIPT.md"
VIDEO = ROOT / "demo" / "anti-radar-demo.mp4"
META = HERE / "test-results" / "demo-tour-meta.json"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
RATE = 48_000

# Scene number -> tour beats (keys of DWELL in demo-tour.spec.ts). Two-beat scenes keep a fixed first beat.
SCENES = {1: ["title"], 2: ["home"], 3: ["formation", "formation-1"], 4: ["collision"], 5: ["why-now"],
          6: ["person"], 7: ["meet", "meet-1"], 8: ["attention"], 9: ["brief"], 10: ["final"]}
FIRST_BEAT = {"formation": 7.0, "meet": 9.0}
SECOND_SETTLE = 1.0   # conservative (short) estimate of a second beat's load time
LEAD = 0.5            # narration starts this long after its scene has settled
TITLE_LEAD = 0.8      # ... and after the title card's entrance
TAIL = 1.0            # quiet time left at the end of each scene
OUTRO = 2.5           # extra hold on the closing card
PAD_IN, PAD_OUT = 0.12, 0.30  # kept around each detected paragraph (soft onsets / trailing consonants)


def sh(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, capture_output=True, **kw)


def paragraphs(n: int = 10) -> list[str]:
    """Scene paragraphs from DEMO_SCRIPT.md (`**N. Title (m:ss).** text`), without markdown."""
    found = re.findall(r"^\*\*(\d+)\. [^*]+\*\*\s*(.+)$", SCRIPT.read_text(), re.M)
    texts = {int(k): re.sub(r"[*`_]", "", v).strip() for k, v in found}
    if sorted(texts) != list(range(1, n + 1)):
        sys.exit(f"voiceover: expected scenes 1..{n} in {SCRIPT}, found {sorted(texts)}")
    return [texts[i] for i in range(1, n + 1)]


def spans(audio: Path, min_pause: float, noise_db: float) -> list[tuple[float, float]]:
    """Speech spans (seconds) between pauses of at least `min_pause`."""
    err = subprocess.run([FFMPEG, "-hide_banner", "-i", str(audio), "-af",
                          f"silencedetect=noise={noise_db}dB:d={min_pause}", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    dur = parse_duration(err)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    out, cursor = [], 0.0
    for s, e in zip(starts, ends + [dur] * (len(starts) - len(ends))):
        if s - cursor > 0.3:
            out.append((cursor, s))
        cursor = e
    if dur - cursor > 0.3:
        out.append((cursor, dur))
    return out


def scene_spans(args) -> list[tuple[float, float]]:
    found = spans(args.audio, args.min_pause, args.noise_db)
    if len(found) != 10:
        listing = "\n".join(f"  {a:7.2f} - {b:7.2f}" for a, b in found)
        sys.exit(f"voiceover: found {len(found)} paragraphs, expected 10 (one per scene):\n{listing}\n"
                 "Adjust --min-pause / --noise-db, or re-record with clearer pauses between paragraphs.")
    return [(max(0.0, a - PAD_IN), b + PAD_OUT) for a, b in found]


def dwell_plan(lengths: list[float]) -> dict[str, float]:
    plan: dict[str, float] = {}
    for scene, beats in SCENES.items():
        need = lengths[scene - 1] + (TITLE_LEAD if scene == 1 else LEAD) + TAIL + (OUTRO if scene == 10 else 0)
        if len(beats) == 1:
            plan[beats[0]] = need
        else:
            first = FIRST_BEAT[beats[0]]
            plan[beats[0]] = first
            plan[beats[1]] = max(5.0, need - first - SECOND_SETTLE)
    return {k: round(v, 1) for k, v in plan.items()}


def cmd_plan(args) -> None:
    sp = scene_spans(args)
    for i, (a, b) in enumerate(sp, 1):
        print(f"  scene {i:2d}: {a:7.2f} - {b:7.2f}  ({b - a:4.1f} s)", file=sys.stderr)
    plan = dwell_plan([b - a for a, b in sp])
    print(",".join(f"{k}={v}" for k, v in plan.items()))


def decode(audio: Path) -> np.ndarray:
    raw = sh([FFMPEG, "-v", "error", "-i", str(audio), "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"]).stdout
    return np.frombuffer(raw, dtype=np.float32)


def parse_duration(ffmpeg_stderr: str) -> float:
    h, m, sec = re.search(r"Duration: (\d+):(\d+):([\d.]+)", ffmpeg_stderr).groups()
    return int(h) * 3600 + int(m) * 60 + float(sec)


def video_duration(path: Path) -> float:
    return parse_duration(subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr)


def scene_windows(meta: dict) -> list[tuple[float, float]]:
    """(on screen, left) per scene, in seconds of the encoded MP4 (record-demo.sh trims take_start_s + 0.2)."""
    offset = meta["take_start_s"] + 0.2
    out = []
    for scene in range(1, 11):
        marks = [m for m in meta["marks"] if m["scene"] == scene]
        out.append((marks[0]["in_s"] - offset, marks[-1]["out_s"] - offset))
    return out


def wrap(text: str, width: int = 46) -> str:
    """At most two balanced lines."""
    if len(text) <= width:
        return text
    words, best = text.split(), None
    for i in range(1, len(text.split())):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        score = max(len(a), len(b))
        if best is None or score < best[0]:
            best = (score, a, b)
    return f"{best[1]}\n{best[2]}"


def chunks(paragraph: str, limit: int = 84) -> list[str]:
    """Caption-sized pieces: sentences, then clauses, then halves."""
    out = []
    for sentence in re.split(r"(?<=[.!?;:])\s+", paragraph):
        pending = [sentence]
        while pending:
            piece = pending.pop(0)
            if len(piece) <= limit:
                out.append(piece)
                continue
            cut = [m.end() for m in re.finditer(r",\s", piece)] or [m.end() for m in re.finditer(r"\s", piece)]
            mid = min(cut, key=lambda c: abs(c - len(piece) / 2))
            pending[:0] = [piece[:mid].strip(), piece[mid:].strip()]
    return out


def srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def cmd_mix(args) -> None:
    meta = json.loads(META.read_text())
    if "marks" not in meta:
        sys.exit("voiceover: demo-tour-meta.json has no scene marks; re-run ./record-demo.sh")
    sp = scene_spans(args)
    windows = scene_windows(meta)
    total = video_duration(VIDEO)
    voice = decode(args.audio)
    words = [w for seg in json.loads(args.words.read_text()) for w in seg["words"]] if args.words else []

    track = np.zeros(int((total + 1) * RATE), dtype=np.float32)
    cues: list[tuple[float, float, str]] = []
    fade = int(0.02 * RATE)
    for i, ((a, b), (on, off), text) in enumerate(zip(sp, windows, paragraphs()), 1):
        place = on + (TITLE_LEAD if i == 1 else LEAD)
        clip = voice[int(a * RATE): int(b * RATE)].copy()
        clip[:fade] *= np.linspace(0, 1, fade)
        clip[-fade:] *= np.linspace(1, 0, fade)
        start = int(place * RATE)
        track[start: start + len(clip)] += clip[: max(0, len(track) - start)]
        end = place + len(clip) / RATE
        status = "ok" if end <= off + 0.4 else f"OVERRUNS the scene by {end - off:.1f} s"
        print(f"  scene {i:2d}: on {on:6.2f}  voice {place:6.2f}-{end:6.2f}  leaves {off:6.2f}  {status}", file=sys.stderr)

        # Captions: script text, timed by recognised word onsets when they cover the paragraph, else evenly.
        pieces = chunks(text)
        n_script = sum(len(p.split()) for p in pieces)
        heard = [w for w in words if a - 0.2 <= w["s"] <= b]
        def at(k: int) -> float:
            if len(heard) >= 0.85 * n_script:
                return heard[min(len(heard) - 1, round(k * len(heard) / n_script))]["s"] - a + place
            return place + (len(clip) / RATE) * k / n_script
        k = 0
        for p in pieces:
            cues.append((at(k), 0.0, p))
            k += len(p.split())
        cues[-1] = (cues[-1][0], end, cues[-1][2])
    # Each cue ends where the next begins (within its paragraph), never shorter than 1 s.
    timed = []
    for j, (s, e, text) in enumerate(cues):
        if not e:
            e = cues[j + 1][0] - 0.05
        timed.append((s, max(e, s + 1.0), text))

    out = VIDEO.with_name("anti-radar-demo-voiceover.mp4")
    srt = out.with_suffix(".srt")
    srt.write_text("\n".join(f"{n}\n{srt_time(s)} --> {srt_time(e)}\n{wrap(t)}\n" for n, (s, e, t) in enumerate(timed, 1)))
    wav = HERE / "test-results" / "voiceover.wav"
    pcm = (np.clip(track[: int(total * RATE)], -1, 1) * 32767).astype("<i2")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())

    voice_chain = "highpass=f=70,afftdn=nf=-28,acompressor=threshold=-20dB:ratio=2.5:attack=10:release=200," \
                  "loudnorm=I=-16:TP=-1.5:LRA=9"
    sh([FFMPEG, "-y", "-v", "error", "-i", str(VIDEO), "-i", str(wav), "-i", str(srt),
        "-map", "0:v", "-map", "1:a", "-map", "2:s", "-c:v", "copy", "-af", voice_chain, "-ar", str(RATE),
        "-ac", "2",  # stereo: some players and headsets play mono AAC in one ear or not at all
        "-c:a", "aac", "-b:a", "160k", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
        "-movflags", "+faststart", str(out)])
    burned = out.with_name("anti-radar-demo-voiceover-captions.mp4")
    # libass scales SRT styles to a 288-line canvas: FontSize 12 ≈ 45 px and MarginV 22 ≈ 82 px at 1080p,
    # which keeps captions above the presenter bar. BorderStyle 3 = translucent box (Outline = padding).
    style = ("FontName=DejaVu Sans,FontSize=12,PrimaryColour=&H00FFFFFF,OutlineColour=&H66000000,"
             "BackColour=&H66000000,BorderStyle=3,Outline=1.6,Shadow=0,MarginV=22,Alignment=2")
    sh([FFMPEG, "-y", "-v", "error", "-i", str(out), "-map", "0:v", "-map", "0:a",
        "-vf", f"subtitles={srt.name}:force_style='{style}'", "-c:v", "libx264", "-preset", "slow", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(burned)], cwd=str(srt.parent))
    for f in (out, burned, srt):
        print(f"voiceover: {f.relative_to(ROOT)}  ({f.stat().st_size / 1e6:.1f} MB)", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "mix"])
    ap.add_argument("--audio", type=Path, required=True)
    ap.add_argument("--words", type=Path, help="word timestamps JSON (optional)")
    ap.add_argument("--min-pause", type=float, default=1.5, help="seconds of silence between paragraphs")
    ap.add_argument("--noise-db", type=float, default=-35, help="silence threshold")
    args = ap.parse_args()
    (cmd_plan if args.command == "plan" else cmd_mix)(args)


if __name__ == "__main__":
    main()
