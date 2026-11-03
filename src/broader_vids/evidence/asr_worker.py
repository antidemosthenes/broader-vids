import json
from pathlib import Path
import subprocess
import sys
import math
import tempfile

from broader_vids.evidence.media import ffmpeg_executable, probe_media


def decode_chunk(media, offset, seconds, target):
    with tempfile.TemporaryFile() as diagnostics:
        decoded = subprocess.run(
            [
                ffmpeg_executable(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-threads",
                "2",
                "-ss",
                str(offset),
                "-i",
                media,
                "-t",
                str(seconds),
                "-vn",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "f32le",
                "pipe:1",
            ],
            stdout=target,
            stderr=diagnostics,
            timeout=120,
        )
    if decoded.returncode:
        raise ValueError("Audio chunk decoding failed.")


def transcribe_chunks(media, model, *, duration=None, decoder=decode_chunk):
    import numpy as np

    duration = probe_media(media)["duration_seconds"] if duration is None else duration
    if not math.isfinite(duration) or not 0 < duration <= 5400:
        raise ValueError(
            "Transcription requires a playable video of at most 90 minutes."
        )
    cues, language, characters = [], None, 0
    for offset in range(0, math.ceil(duration), 600):
        seconds = min(600, duration - offset)
        with tempfile.TemporaryFile() as decoded:
            decoder(media, offset, seconds, decoded)
            size = decoded.seek(0, 2)
            if not 0 < size <= math.ceil(seconds * 16000) * 4 + 65536 or size % 4:
                raise ValueError("Audio chunk exceeds its bounded sample count.")
            decoded.seek(0)
            audio = np.fromfile(decoded, dtype=np.float32)
        segments, info = model.transcribe(audio, beam_size=1, vad_filter=True)
        language = language or info.language
        try:
            for segment in segments:
                start, end = max(0, segment.start), min(seconds, segment.end)
                text = segment.text.strip()
                if (
                    not text
                    or not math.isfinite(start)
                    or not math.isfinite(end)
                    or end <= start
                ):
                    continue
                characters += len(text)
                if len(cues) >= 50000 or characters > 500000:
                    raise ValueError("Transcription exceeds the bounded caption size.")
                cues.append(
                    {
                        "id": f"C{len(cues) + 1:05d}",
                        "start": round(offset + start, 3),
                        "end": round(offset + end, 3),
                        "text": text,
                    }
                )
        finally:
            del segments, audio
    if not cues:
        raise ValueError("ASR produced no speech evidence.")
    return cues, language


def main():
    from faster_whisper import WhisperModel
    from broader_vids.evidence.service import atomic_json

    media, target, model_name, model_directory = sys.argv[1:]
    model = WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        cpu_threads=2,
        download_root=model_directory,
    )
    cues, language = transcribe_chunks(media, model)
    atomic_json(Path(target), cues)
    print(json.dumps({"language": language, "cue_count": len(cues)}))


if __name__ == "__main__":
    main()
