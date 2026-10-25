import json
from pathlib import Path
import subprocess
import sys

from .evidence_media import ffmpeg_executable


def main():
    import numpy as np
    from faster_whisper import WhisperModel
    media, target, model_name, model_directory = sys.argv[1:]
    decoded = subprocess.run([ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", media,
                              "-vn", "-ar", "16000", "-ac", "1", "-f", "f32le", "pipe:1"],
                             capture_output=True, timeout=120)
    if decoded.returncode or not decoded.stdout or len(decoded.stdout) > 5400 * 16000 * 4 + 65536:
        raise ValueError("Audio decoding failed or exceeded the 90-minute bound.")
    audio = np.frombuffer(decoded.stdout, dtype=np.float32)
    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=4, download_root=model_directory)
    segments, info = model.transcribe(audio, beam_size=1, vad_filter=True)
    cues = []
    for segment in segments:
        if segment.text.strip():
            cues.append({"id": f"C{len(cues) + 1:05d}", "start": round(segment.start, 3), "end": round(segment.end, 3), "text": segment.text.strip()})
    if not cues:
        raise ValueError("ASR produced no speech evidence.")
    path = Path(target)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(cues, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    print(json.dumps({"language": info.language, "cue_count": len(cues)}))


if __name__ == "__main__":
    main()
