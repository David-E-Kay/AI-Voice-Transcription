"""A/B benchmark: Whisper vs Phonon-2 on the same recordings of your own voice.

    python bench_engines.py record     # read ~12 prompts aloud; saves bench/*.wav + refs.json
    python bench_engines.py run        # both engines transcribe the same clips -> WER + speed

Stop the dictation app first (Stop-ScheduledTask "AI Voice Dictation") so Whisper's VRAM
reading and the GPU timing aren't shared with a second copy of the model.
"""
import argparse
import gc
import json
import os
import shutil
import statistics
import subprocess
import time
import wave

import numpy as np

from dictate_core import word_errors

SAMPLE_RATE = 16000
BENCH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench")
REFS = os.path.join(BENCH_DIR, "refs.json")

# Edit freely to match what you actually dictate. Dictation-shaped prompts: names, tech terms, numbers, a run-on sentence, a short reply.
PROMPTS = [
    "Can you check whether the summarizer is still running on the GPU after the last update?",
    "Please refactor the engine class so the model loads once and stays warm between calls.",
    "Send a quick note to the team saying the dashboard will be down for maintenance tonight.",
    "I think the bug is in the keyboard hook, because it only happens when I hold the key down.",
    "Add a unit test that covers the empty clipboard case and make sure it runs on Windows.",
    "The meeting moved to Thursday at three thirty, so let's push the review to next week.",
    "Open the pull request, read the review comments, and fix anything that's marked as blocking.",
    "Yes, that works for me.",
    "We should compare latency and accuracy before we decide which model to keep running in the background.",
    "Summarize the last five sessions and highlight anything that failed or needed a retry.",
    "My laptop only has four gigabytes of video memory, so every model has to share it carefully.",
    "Refresh the Power BI report and check that the DAX measure for monthly permits still matches the source table.",
    "Ask the city clerk whether the council agenda for the zoning hearing has been posted yet.",
    "Remind me tomorrow morning to update the README with the new environment variable.",
]


def _record_clip():
    import sounddevice as sd
    frames = []
    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                            callback=lambda indata, *_: frames.append(indata.copy()))
    input("   [Enter] to start recording...")
    stream.start()
    input("   recording - [Enter] to stop")
    stream.stop()
    stream.close()
    return np.concatenate(frames).reshape(-1) if frames else np.zeros(0, np.float32)


def _write_wav(path, audio):
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


def _read_wav(path):
    with wave.open(path, "rb") as w:
        assert w.getframerate() == SAMPLE_RATE and w.getnchannels() == 1, path
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768


def record():
    os.makedirs(BENCH_DIR, exist_ok=True)
    refs = {}
    print(f"Read each prompt aloud at your normal dictation pace. {len(PROMPTS)} clips.\n")
    for i, text in enumerate(PROMPTS, 1):
        name = f"clip_{i:02d}.wav"
        while True:
            print(f"{i}/{len(PROMPTS)}: {text}")
            audio = _record_clip()
            if len(audio) >= SAMPLE_RATE // 2:
                break
            print("   too short, again.")
        _write_wav(os.path.join(BENCH_DIR, name), audio)
        refs[name] = text
        print(f"   saved {name} ({len(audio) / SAMPLE_RATE:.1f} s)\n")
    with open(REFS, "w", encoding="utf-8") as f:
        json.dump(refs, f, indent=1)
    print(f"Done. Edit {REFS} if you misspoke, so the reference matches what you said.")


def _vram_used_mib():
    if not shutil.which("nvidia-smi"):
        return None
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout
    return int(out.split()[0]) if out.strip() else None


def _make(name):
    import dictate  # deferred: pulls in keyboard/sounddevice and the engines
    return dictate.PhononEngine() if name == "phonon" else dictate.Engine()


def run(engines, repeats):
    with open(REFS, encoding="utf-8") as f:
        refs = json.load(f)
    clips = {n: _read_wav(os.path.join(BENCH_DIR, n)) for n in refs}
    audio_s = sum(len(a) for a in clips.values()) / SAMPLE_RATE
    results = {}
    for name in engines:
        print(f"\n== {name}: loading...")
        vram0 = _vram_used_mib()
        t0 = time.perf_counter()
        eng = _make(name)
        load_s = time.perf_counter() - t0
        vram1 = _vram_used_mib()
        errs = words = 0
        lat, per_clip = [], {}
        for clip, audio in clips.items():
            times = []
            for _ in range(repeats):
                t = time.perf_counter()
                hyp = eng.transcribe(audio).strip()
                times.append(time.perf_counter() - t)
            e, n = word_errors(refs[clip], hyp)
            errs, words = errs + e, words + n
            lat.append(statistics.median(times))
            per_clip[clip] = {"hyp": hyp, "errors": e, "words": n, "latency_s": lat[-1]}
            print(f"   {clip}  {lat[-1]:.2f} s  {e} err  {hyp}")
        results[name] = {
            "wer": errs / words, "errors": errs, "words": words, "load_s": load_s,
            "median_latency_s": statistics.median(lat), "max_latency_s": max(lat),
            "realtime_x": audio_s / sum(lat),
            "vram_mib": None if vram0 is None or vram1 is None else vram1 - vram0,
            "clips": per_clip,
        }
        del eng
        gc.collect()
        time.sleep(2)  # let the GPU allocator release before the next engine's VRAM reading

    print(f"\n{len(clips)} clips, {audio_s:.0f} s of audio, {repeats} timed runs each (median)\n")
    print(f"{'engine':8} {'WER':>6} {'errors':>8} {'median':>8} {'worst':>7} {'speed':>8} {'load':>6} {'VRAM':>8}")
    for name, r in results.items():
        vram = "n/a" if r["vram_mib"] is None else f"{r['vram_mib']} MiB"
        print(f"{name:8} {r['wer']:6.1%} {r['errors']:>4}/{r['words']:<3} {r['median_latency_s']:7.2f}s "
              f"{r['max_latency_s']:6.2f}s {r['realtime_x']:7.0f}x {r['load_s']:5.1f}s {vram:>8}")
    out = os.path.join(BENCH_DIR, "results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    print(f"\nPer-clip transcripts: {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("record", help="record the prompt set")
    r = sub.add_parser("run", help="transcribe the recordings with each engine")
    r.add_argument("--engines", default="phonon,whisper", help="comma list, run in this order")
    r.add_argument("--repeats", type=int, default=3, help="timed runs per clip (median kept)")
    a = ap.parse_args()
    record() if a.cmd == "record" else run(a.engines.split(","), a.repeats)


if __name__ == "__main__":
    main()
