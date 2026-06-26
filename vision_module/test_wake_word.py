"""
Test script for "Hey Kanda" wake word detection.
Uses the trained hey_kanda.onnx model with openWakeWord.

Usage:
    python3 test_wake_word.py

Say "Hey Kanda" into your microphone. The script will print
detection events with confidence scores for 60 seconds.
"""

import os
import sys
import threading
import time

import numpy as np

OWW_SAMPLE_RATE = 16000
OWW_CHUNK_FRAMES = 1280
MODEL_PATH = os.path.join(os.path.dirname(__file__), "hey_kanda.onnx")
SENSITIVITY = 0.1

def main():
    print("=" * 50)
    print("  Hey Kanda — Wake Word Test")
    print("=" * 50)
    print()

    if not os.path.isfile(MODEL_PATH):
        print(f"ERROR: Model not found at {MODEL_PATH}")
        sys.exit(1)
    print(f"  Model : {MODEL_PATH} ({os.path.getsize(MODEL_PATH) // 1024} KB)")
    print(f"  Threshold : {SENSITIVITY}")
    print()

    # Load openWakeWord
    try:
        from openwakeword.model import Model
    except ImportError:
        print("ERROR: openwakeword not installed")
        print("  Run: pip install openwakeword")
        sys.exit(1)

    try:
        import pyaudio
    except ImportError:
        print("ERROR: pyaudio not installed")
        print("  Run: pip install pyaudio")
        sys.exit(1)

    # Load model
    print("Loading model...")
    try:
        oww = Model(wakeword_models=[MODEL_PATH])
    except Exception as e:
        print(f"ERROR loading model: {e}")
        sys.exit(1)

    wake_label = "hey_kanda"
    print(f"  Model loaded! Wake label: '{wake_label}'")
    print()

    # Open mic
    pa = pyaudio.PyAudio()

    mic_rate = OWW_SAMPLE_RATE
    for try_rate in [16000, 44100, 48000]:
        try:
            test = pa.open(rate=try_rate, channels=1, format=pyaudio.paInt16,
                           input=True, frames_per_buffer=OWW_CHUNK_FRAMES)
            test.close()
            mic_rate = try_rate
            break
        except Exception:
            continue

    read_frames = int(mic_rate * OWW_CHUNK_FRAMES / OWW_SAMPLE_RATE)
    stream = pa.open(
        rate=mic_rate,
        channels=1,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=read_frames,
    )

    print(f"  Mic rate: {mic_rate} Hz")
    print()
    print("━" * 50)
    print("  Say \"Hey Kanda\" now! (60 seconds)")
    print("━" * 50)
    print()

    detections = 0
    start = time.time()
    duration = 60
    last_level_print = 0
    max_score_seen = 0.0

    try:
        while time.time() - start < duration:
            remaining = int(duration - (time.time() - start))

            raw = stream.read(read_frames, exception_on_overflow=False)
            pcm_raw = np.frombuffer(raw, dtype=np.int16)

            # Show audio level every ~0.5s so we know mic is working
            rms = int(np.sqrt(np.mean(pcm_raw.astype(np.float32) ** 2)))
            now = time.time()
            if now - last_level_print > 0.5:
                level_bar = "▮" * min(40, rms // 200)
                print(f"\r  [{remaining:2d}s] mic={rms:5d} {level_bar:<40s}", end="", flush=True)
                last_level_print = now

            pcm = pcm_raw.astype(np.float32)

            # Resample to 16kHz if needed
            if mic_rate != OWW_SAMPLE_RATE:
                indices = np.linspace(0, len(pcm) - 1, OWW_CHUNK_FRAMES).astype(int)
                pcm = pcm[indices]

            predictions = oww.predict(pcm)
            score = predictions.get(wake_label, 0.0)
            max_score_seen = max(max_score_seen, score)

            # Show scores above noise floor
            if score > 0.01:
                bar = "█" * int(score * 50)
                status = " ← DETECTED!" if score >= SENSITIVITY else ""
                print(f"\r  [{remaining:2d}s] score={score:.4f} {bar}{status}                    ")

            if score >= SENSITIVITY:
                detections += 1
                print()
                print(f"  ✓ WAKE WORD DETECTED! (score={score:.3f}, count={detections})")
                print()
                oww.reset()
                time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n  Stopped by user.")
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()

    print()
    print()
    print("━" * 50)
    print(f"  Results: {detections} detections in {int(time.time() - start)}s")
    print(f"  Max score seen: {max_score_seen:.4f} (threshold: {SENSITIVITY})")
    if detections > 0:
        print("  ✓ Hey Kanda wake word is working!")
    else:
        if max_score_seen < 0.001:
            print("  ✗ Scores stayed near zero — mic may not be")
            print("    capturing audio. Check macOS mic permissions:")
            print("    System Settings → Privacy → Microphone → Terminal")
        else:
            print(f"  ✗ Best score was {max_score_seen:.4f} — try speaking")
            print("    louder, or lower SENSITIVITY at top of script")
    print("━" * 50)


if __name__ == "__main__":
    main()
