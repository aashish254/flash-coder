"""Voice spike (Phase 4): measure local STT + TTS latency on Apple Silicon.

Question: is hands-free voice control of Flash Coder viable at interactive
latency? PLAN.md Phase 4 wants whisper.cpp-class STT + Piper/Kokoro-class TTS,
full-duplex ("stop", "revert"). This spike measures the actual loop on the M5
using mlx-audio (native MLX ports — no whisper.cpp/Piper needed):

  speak -> STT (whisper MLX) -> [agent decides] -> TTS ack (Kokoro MLX)

We synthesize the test audio with the TTS itself (self-consistent: no mic
needed, and the TTS output is exactly the kind of clean speech the STT must
handle). Metrics: cold + warm latency, real-time factor, transcription
accuracy on a command phrase, and the projected round-trip.

Honesty notes: TTS-generated speech is easier than real mic audio (no noise,
no accent); treat STT numbers as a LOWER bound on latency and an UPPER bound
on accuracy. Mic capture adds ~0; background noise is the unmeasured risk.

Run: .venv/bin/python benchmarks/voice_spike.py
"""

import argparse
import difflib
import json
import statistics
import time
from pathlib import Path

COMMAND_PHRASE = "flash revert the last change and rerun the tests"
# The phrase mixes a wake word, a verb command, and a conjunction — the shape
# of real hands-free control utterances (short, imperative, jargon-free).

OUT = Path("/tmp/voice_spike")
TTS_MODEL = "mlx-community/Kokoro-82M-bf16"
STT_MODELS = [
    "mlx-community/whisper-base-mlx",          # tiny/fast tier
    "mlx-community/whisper-large-v3-turbo",    # accurate tier (default STT pick)
]


def tts(phrase: str, reps: int) -> dict:
    from mlx_audio.tts.generate import generate_audio
    OUT.mkdir(exist_ok=True)
    wav = OUT / "phrase.wav"

    t0 = time.time()  # cold: includes model load on first call
    generate_audio(phrase, model=TTS_MODEL, voice="af_heart",
                   output_path=str(OUT / "cold"), file_prefix="phrase",
                   audio_format="wav", join_audio=True, verbose=False)
    cold = time.time() - t0

    warms = []
    for i in range(reps):
        t0 = time.time()
        generate_audio(phrase, model=TTS_MODEL, voice="af_heart",
                       output_path=str(OUT), file_prefix="phrase",
                       audio_format="wav", join_audio=True, verbose=False)
        warms.append(time.time() - t0)

    import soundfile as sf
    data, sr = sf.read(str(wav))
    dur = len(data) / sr
    return {"wav": str(wav), "audio_s": round(dur, 2),
            "cold_s": round(cold, 2),
            "warm_median_s": round(statistics.median(warms), 3),
            "warm_rtf": round(statistics.median(warms) / dur, 3),
            "warm_runs_s": [round(w, 3) for w in warms]}


def stt(wav: str, model: str, phrase: str, reps: int) -> dict:
    from mlx_audio.stt.generate import generate_transcription
    import mlx_audio.stt.utils as stt_utils

    m = stt_utils.load_model(model)  # load once, outside the timed loop
    # mlx-community whisper repos ship weights only — no processor files, so
    # mlx_audio's post_load_hook leaves model._processor=None and decode dies.
    # Attach the processor from the matching openai/whisper-* repo instead.
    if getattr(m, "_processor", None) is None:
        from transformers import WhisperProcessor
        upstream = "openai/" + model.split("/")[-1].removesuffix("-mlx")
        m._processor = WhisperProcessor.from_pretrained(upstream)
    out = generate_transcription(model=m, audio=wav, verbose=False)
    cold_text = out.text.strip() if hasattr(out, "text") else str(out).strip()

    warms, texts = [], []
    for _ in range(reps):
        t0 = time.time()
        out = generate_transcription(model=m, audio=wav, verbose=False)
        warms.append(time.time() - t0)
        texts.append(out.text.strip())

    sim = difflib.SequenceMatcher(
        None, phrase.lower(), texts[-1].lower()).ratio()
    return {"model": model, "heard": texts[-1],
            "similarity": round(sim, 3),
            "cold_text": cold_text,
            "warm_median_s": round(statistics.median(warms), 3),
            "warm_runs_s": [round(w, 3) for w in warms]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--phrase", default=COMMAND_PHRASE)
    ap.add_argument("--stt", nargs="*", default=STT_MODELS)
    args = ap.parse_args()

    print(f"PHRASE: {args.phrase!r}  reps={args.reps}\n")

    print("== TTS (Kokoro MLX) ==")
    t = tts(args.phrase, args.reps)
    print(json.dumps({k: v for k, v in t.items() if k != "wav"}, indent=2))

    stt_results = []
    for model in args.stt:
        print(f"\n== STT ({model}) ==")
        r = stt(t["wav"], model, args.phrase, args.reps)
        print(json.dumps({k: v for k, v in r.items() if k != "cold_text"},
                         indent=2))
        stt_results.append(r)

    print("\n== ROUND-TRIP (speak -> hear -> ack starts) ==")
    for r in stt_results:
        # user speaks (audio duration) -> STT warm -> TTS time-to-first-audio.
        # Kokoro streams by sentence; first-chunk latency ~= warm / sentences.
        # Here: 1 sentence, so TTS warm ~= full synth. Conservative.
        rt = t["audio_s"] + r["warm_median_s"] + t["warm_median_s"]
        print(f"  {r['model']}: {rt:.2f}s "
              f"(speak {t['audio_s']}s + stt {r['warm_median_s']}s "
              f"+ tts {t['warm_median_s']}s)")

    print("\n== VERDICT ==")
    best = min(stt_results, key=lambda r: r["warm_median_s"])
    ok = (best["warm_median_s"] < 1.0 and t["warm_median_s"] < 1.0
          and best["similarity"] > 0.9)
    print(f"  fastest accurate STT: {best['model']} "
          f"({best['warm_median_s']}s, sim {best['similarity']})")
    print(f"  TTS warm: {t['warm_median_s']}s for {t['audio_s']}s audio "
          f"(RTF {t['warm_rtf']})")
    print(f"  full-duplex control (<1s STT + <1s ack): "
          f"{'VIABLE' if ok else 'NOT YET — see numbers'}")
    print("  caveat: TTS-clean speech; mic noise unmeasured (next spike)")


if __name__ == "__main__":
    main()
