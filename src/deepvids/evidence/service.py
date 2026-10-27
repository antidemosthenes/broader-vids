from contextlib import nullcontext
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tempfile
import threading

from deepvids.evidence.browser import bilibili_browser, browser_cookie_jar
from deepvids.evidence.media import (
    EvidencePending,
    VideoFetcher,
    compact_captions,
    extract_frame,
    file_hash,
    image_fingerprint,
    parse_captions,
    probe_media,
)
from deepvids.storage import utc_now
from deepvids.processes import run_bounded
from deepvids.identity import identify
from deepvids.evidence.retention import released_media, inherit_bundle


EVIDENCE_VERSION = 1
_JSON_REPLACE_LOCK = threading.Lock()


def atomic_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".tmp", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        with _JSON_REPLACE_LOCK:
            deadline = time.monotonic() + 0.5
            while True:
                try:
                    temporary.replace(path)
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def create_evidence(
    store,
    url,
    media=None,
    captions=None,
    use_browser=False,
    max_mib=128,
    max_seconds=1200,
    originality_context=None,
    reasoning_effort="low",
):
    identity = identify(url)
    if reasoning_effort not in {"low", "medium"}:
        raise ValueError("Reasoning effort must be low or medium.")
    if (
        type(max_mib) is not int
        or not 1 <= max_mib <= 512
        or not 0 < max_seconds <= 3600
    ):
        raise ValueError(
            "Evidence budgets allow 1–512 MiB and at most 3,600 seconds per invocation."
        )
    if captions and not media:
        raise ValueError("Local captions require the corresponding local video file.")
    settings = {
        "kind": "evidence",
        "schema": EVIDENCE_VERSION,
        "identity": identity,
        "max_seconds": max_seconds,
        "max_bytes": max_mib * 1024 * 1024,
        "use_browser": bool(use_browser),
        "local_media": str(Path(media).resolve()) if media else None,
        "local_captions": str(Path(captions).resolve()) if captions else None,
        "initial_fractions": [0.1, 0.35, 0.65, 0.9],
        "max_images": 8,
        "max_model_calls": 2,
        "caption_character_limit": 12000,
        "rubric_version": 3,
        "originality_context": originality_context,
        "reasoning_effort": reasoning_effort,
    }
    for key in ("local_media", "local_captions"):
        if settings[key] and (
            not Path(settings[key]).is_file()
            or Path(settings[key]).stat().st_size > settings["max_bytes"]
        ):
            raise ValueError(
                "Local evidence must be a file within the configured size limit."
            )
    fingerprints = {
        key: file_hash(settings[key]) if settings[key] else None
        for key in ("local_media", "local_captions")
    }
    cache_key = hashlib.sha256(
        json.dumps(
            {
                "identity": identity,
                "files": fingerprints,
                "version": EVIDENCE_VERSION,
                **(
                    {"originality_context": originality_context}
                    if originality_context
                    else {}
                ),
                **(
                    {"reasoning_effort": reasoning_effort}
                    if reasoning_effort != "low"
                    else {}
                ),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    reusable = None
    existing = None
    for row in store.db.execute(
        "SELECT id FROM runs WHERE json_extract(settings,'$.cache_key')=? AND platform=? ORDER BY created_at",
        (cache_key, identity["platform"]),
    ):
        previous = store.get(row[0])
        if (
            use_browser
            and identity["platform"] == "bilibili"
            and not previous["settings"]["use_browser"]
        ):
            continue
        if (
            not media
            and (originality_context or reasoning_effort != "low")
            and previous["state"]["phase"] == "prepare"
            and not previous["state"].get("media")
        ):
            existing = previous
            break
        return previous["id"]
    if not media and (originality_context or reasoning_effort != "low"):
        row = store.db.execute(
            "SELECT id FROM runs WHERE json_extract(settings,'$.kind')='evidence' AND json_extract(settings,'$.identity.canonical_id')=? AND platform=? AND json_extract(settings,'$.local_media') IS NULL AND json_extract(state,'$.media') IS NOT NULL AND json_extract(state,'$.caption_excerpt') IS NOT NULL AND json_extract(state,'$.phase') IN ('prepared','completed') ORDER BY created_at DESC LIMIT 1",
            (identity["canonical_id"], identity["platform"]),
        ).fetchone()
        reusable = store.get(row[0]) if row else None
    if existing and not reusable:
        return existing["id"]
    settings["cache_key"] = cache_key
    source_lease = store.lease(reusable["id"]) if reusable else nullcontext(None)
    with source_lease as owner:
        identifier = (
            existing["id"]
            if existing
            else store.create(
                identity["platform"],
                settings,
                {
                    "phase": "prepare",
                    "metadata": None,
                    "media": None,
                    "captions": None,
                    "frames": [],
                    "frame_positions_done": [],
                    "assessments": {},
                    "attempts": [],
                    "submitted_images": 0,
                    "model_calls": 0,
                    "resources": {
                        "media_download_bytes": 0,
                        "caption_download_bytes": 0,
                        "preparation_seconds": 0,
                        "transcription_seconds": 0,
                    },
                },
            )
        )
        if reusable:
            run = store.get(identifier)
            inherit_bundle(store, reusable, run, source_owner=owner)
    return identifier


def evidence_root(store, identifier):
    store.validate_id(identifier)
    root = store.root / "evidence" / identifier
    root.mkdir(parents=True, exist_ok=True)
    return root


def _local_captions(path, duration, target):
    path = Path(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise EvidencePending("Local caption files must be at most 2 MiB.")
    cues = parse_captions(
        path.read_text(encoding="utf-8-sig"), path.suffix.lstrip("."), duration
    )
    if not cues:
        raise EvidencePending(
            "The local caption file has no supported timestamped speech cues."
        )
    atomic_json(target, cues)
    return {
        "source": "user_supplied",
        "language": "unspecified",
        "path": str(target),
        "sha256": file_hash(target),
        "cue_count": len(cues),
        "download_bytes": 0,
    }


def transcribe_media(media, target, model_name, model_directory, deadline):
    if importlib.util.find_spec("faster_whisper") is None:
        raise EvidencePending(
            "Captions are unavailable; install the optional asr extra or provide timestamped local captions."
        )
    if model_name not in {"tiny", "base", "small"}:
        raise ValueError("The bounded ASR trial supports tiny, base or small models.")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise EvidencePending("The local transcription time budget is exhausted.")
    process = run_bounded(
        [
            sys.executable,
            "-m",
            "deepvids.evidence.asr_worker",
            str(media),
            str(target),
            model_name,
            str(model_directory),
        ],
        timeout=remaining,
    )
    if process.returncode or not target.is_file():
        raise EvidencePending(
            "The local ASR worker failed; no speech evidence was invented."
        )
    metadata = json.loads(process.stdout.strip().splitlines()[-1])
    return {
        "source": "local_asr",
        "model": model_name,
        "language": metadata["language"],
        "path": str(target),
        "sha256": file_hash(target),
        "cue_count": metadata["cue_count"],
        "download_bytes": 0,
        "text_accuracy": "unverified",
    }


def prepare_evidence(
    store,
    identifier,
    asr_model=None,
    fetcher_type=VideoFetcher,
    *,
    max_seconds=None,
    max_new_bytes=None,
    cookie_jar=None,
    cancel_event=None,
    lease_owner=None,
):
    if asr_model not in {None, "tiny", "base", "small"}:
        raise ValueError("The bounded ASR trial supports tiny, base or small models.")
    if max_seconds is not None and (not math.isfinite(max_seconds) or max_seconds <= 0):
        raise ValueError("The remaining preparation time must be positive and finite.")
    if max_new_bytes is not None and (
        type(max_new_bytes) is not int or max_new_bytes < 0
    ):
        raise ValueError(
            "The remaining download allowance must be a nonnegative integer."
        )
    with store.lease(identifier, owner=lease_owner):
        run = store.get(identifier)
        if run["settings"].get("kind") != "evidence":
            raise ValueError("Not an evidence run.")
        if run["state"]["phase"] in {"prepared", "completed"}:
            verify_bundle(store, run)
            return run
        started = time.monotonic()
        remaining = run["settings"]["max_seconds"] - run["elapsed"]
        if max_seconds is not None:
            remaining = min(remaining, max_seconds)
        deadline = started + max(0, remaining)
        state, settings = run["state"], run["settings"]
        if asr_model:
            state["asr_model"] = asr_model
        asr_model = state.get("asr_model")
        state["retryable_failure"] = False
        root = evidence_root(store, identifier)

        def save(event=None):
            run["elapsed"] += time.monotonic() - save.last
            save.last = time.monotonic()
            store.save(run, event)

        save.last = started
        fetcher = None
        run.update(status="running", reason="")
        try:
            if remaining <= 0:
                raise EvidencePending("The evidence invocation budget is exhausted.")
            browser = (
                bilibili_browser(store.root)
                if settings["use_browser"]
                and cookie_jar is None
                and not settings["local_media"]
                and not state["media"]
                and run["platform"] == "bilibili"
                else nullcontext(None)
            )
            with browser as context:
                fetcher = None
                info = None
                if not settings["local_media"] and not state["media"]:
                    remaining_bytes = (
                        settings["max_bytes"]
                        - state["resources"]["media_download_bytes"]
                    )
                    if max_new_bytes is not None:
                        remaining_bytes = min(remaining_bytes, max_new_bytes)
                    if remaining_bytes <= 0:
                        raise EvidencePending(
                            "The cumulative media-download byte budget is exhausted.",
                            retryable=True,
                        )
                    fetcher = fetcher_type(
                        remaining_bytes,
                        deadline,
                        browser_cookie_jar(context) if context else cookie_jar,
                    )
                    fetcher.cancelled = cancel_event
                    info = fetcher.metadata(settings["identity"])
                    state["metadata"] = {
                        "title": info.get("title", ""),
                        "duration_seconds": info["duration"],
                        "extractor_id": str(info.get("id", "")),
                        "requested_part": settings["identity"]["part_index"],
                        "source": "yt_dlp_playable_unit",
                        "resolved_at": utc_now(),
                    }
                    save(("evidence_metadata", state["metadata"]))
                    if not state["captions"]:
                        try:
                            state["captions"] = fetcher.captions(
                                info, root, run["platform"]
                            )
                        except EvidencePending as error:
                            state["caption_access_issue"] = str(error)
                        if state["captions"]:
                            state["resources"]["caption_download_bytes"] += state[
                                "captions"
                            ]["download_bytes"]
                            save()
                    try:
                        media = fetcher.download(info, root)
                    finally:
                        state["resources"]["media_download_bytes"] += (
                            fetcher.downloaded_bytes
                        )
                elif settings["local_media"] and not state["media"]:
                    media = root / (
                        "media" + Path(settings["local_media"]).suffix.lower()
                    )
                    shutil.copyfile(settings["local_media"], media)
                else:
                    media = Path(state["media"]["path"])
                if not state["media"]:
                    probe = probe_media(
                        media, timeout=min(30, max(1, deadline - time.monotonic()))
                    )
                    if state["metadata"] and abs(
                        probe["duration_seconds"]
                        - state["metadata"]["duration_seconds"]
                    ) > max(3, state["metadata"]["duration_seconds"] * 0.01):
                        raise EvidencePending(
                            "Downloaded duration does not match the resolved playable unit; a preview or incomplete file cannot pass."
                        )
                    if not state["metadata"]:
                        state["metadata"] = {
                            "title": settings["identity"]["video_id"],
                            "duration_seconds": probe["duration_seconds"],
                            "source": "user_supplied_file",
                            "requested_part": settings["identity"]["part_index"],
                        }
                    state["media"] = {
                        "path": str(media),
                        "sha256": file_hash(media),
                        "bytes": media.stat().st_size,
                        **probe,
                    }
                    save(
                        (
                            "evidence_media",
                            {
                                "sha256": state["media"]["sha256"],
                                "duration_seconds": probe["duration_seconds"],
                            },
                        )
                    )
            if settings["local_captions"] and not state["captions"]:
                state["captions"] = _local_captions(
                    settings["local_captions"],
                    state["media"]["duration_seconds"],
                    root / "captions.json",
                )
                save()
            fingerprints = [int(frame["fingerprint"]) for frame in state["frames"]]
            for fraction in settings["initial_fractions"]:
                if fraction in state["frame_positions_done"]:
                    continue
                if time.monotonic() > deadline:
                    raise EvidencePending(
                        "The frame extraction time budget is exhausted."
                    )
                timestamp = round(fraction * state["media"]["duration_seconds"], 3)
                target = root / f"frame-{len(state['frames']) + 1}.jpg"
                extract_frame(
                    state["media"]["path"],
                    timestamp,
                    target,
                    timeout=min(30, max(0.1, deadline - time.monotonic())),
                )
                fingerprint = image_fingerprint(target)
                if any(
                    (fingerprint ^ previous).bit_count() <= 2
                    for previous in fingerprints
                ):
                    target.unlink()
                    state["frame_positions_done"].append(fraction)
                    save()
                    continue
                fingerprints.append(fingerprint)
                state["frames"].append(
                    {
                        "id": f"F{len(state['frames']) + 1:02d}",
                        "timestamp": timestamp,
                        "path": str(target),
                        "sha256": file_hash(target),
                        "fingerprint": str(fingerprint),
                    }
                )
                state["frame_positions_done"].append(fraction)
                save(("evidence_frames", {"frame_count": len(state["frames"])}))
            if not state["captions"]:
                if not asr_model:
                    raise EvidencePending(
                        "Visual evidence is saved, but no captions were available. Enable local ASR or provide local captions."
                    )
                asr_started = time.monotonic()
                try:
                    state["captions"] = transcribe_media(
                        state["media"]["path"],
                        root / "captions.json",
                        asr_model,
                        store.root / "models",
                        deadline,
                    )
                finally:
                    state["resources"]["transcription_seconds"] += (
                        time.monotonic() - asr_started
                    )
                save()
            cues = json.loads(
                Path(state["captions"]["path"]).read_text(encoding="utf-8")
            )
            compact = compact_captions(cues, settings["caption_character_limit"])
            atomic_json(root / "caption-excerpts.json", compact)
            state["caption_excerpt"] = {
                "path": str(root / "caption-excerpts.json"),
                "sha256": file_hash(root / "caption-excerpts.json"),
                "selected_cues": len(compact),
                "total_cues": len(cues),
                "characters": sum(len(c["text"]) for c in compact),
                "temporal_gaps": len(compact) < len(cues),
            }
            if not state["frames"] or not compact:
                raise EvidencePending(
                    "A usable caption/frame bundle was not established."
                )
            state["phase"] = "prepared"
            run.update(
                status="prepared",
                reason="Timestamped captions and bounded frames are ready for assessment.",
            )
        except (EvidencePending, ValueError) as error:
            state["retryable_failure"] = getattr(error, "retryable", False)
            run.update(status="pending", reason=str(error))
        except (OSError, subprocess.TimeoutExpired) as error:
            state["retryable_failure"] = True
            run.update(
                status="pending",
                reason=f"Evidence preparation failed ({type(error).__name__}); partial work is preserved.",
            )
        except Exception:
            run["state"] = store.get(identifier)["state"]
            state = run["state"]
            run.update(
                status="pending",
                reason="Unexpected preparation failure; the durable checkpoint is preserved.",
            )
            raise
        finally:
            try:
                if fetcher is not None and hasattr(fetcher, "close"):
                    fetcher.close()
            finally:
                state["resources"]["preparation_seconds"] += time.monotonic() - started
                save()
        return store.get(identifier)


def verify_bundle(store, run, *, require_media=False):
    state = run["state"]
    for record in [
        state.get("media"),
        state.get("captions"),
        state.get("caption_excerpt"),
        *state.get("frames", []),
    ]:
        if record is None:
            raise ValueError("The evidence bundle is incomplete.")
        path = Path(record["path"]).resolve()
        if (
            record is state.get("media")
            and not require_media
            and released_media(state)
            and not path.exists()
            and path.is_relative_to(evidence_root(store, run["id"]).resolve())
        ):
            continue
        if (
            not path.is_relative_to(evidence_root(store, run["id"]).resolve())
            or not path.is_file()
            or file_hash(path) != record["sha256"]
        ):
            raise ValueError(
                "Evidence is missing, altered or outside its run directory; assessment is blocked."
            )


def add_evidence_frame(store, identifier, timestamp, reason, long_edge=768):
    if (
        not isinstance(timestamp, (int, float))
        or not math.isfinite(timestamp)
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 500
    ):
        raise ValueError(
            "Targeted frames require a finite timestamp and a brief concrete uncertainty."
        )
    if type(long_edge) is not int or not 768 <= long_edge <= 1536:
        raise ValueError(
            "Targeted frame resolution must be between 768 and 1536 pixels on the long edge."
        )
    with store.lease(identifier):
        run = store.get(identifier)
        verify_bundle(store, run)
        state = run["state"]
        if "augmented" in state["assessments"]:
            raise ValueError(
                "Targeted evidence must be requested before the augmented trial assessment."
            )
        if released_media(state):
            raise ValueError(
                "The full video was released after assessment; additional frames require a new explicit local-media evidence job."
            )
        if (
            len(state["frames"]) >= 8
            or not 0 <= timestamp < state["media"]["duration_seconds"]
        ):
            raise ValueError("Frame timestamp or eight-image trial limit exceeded.")
        remaining = run["settings"]["max_seconds"] - run["elapsed"]
        if remaining <= 0:
            raise ValueError("The original evidence time budget is exhausted.")
        started = time.monotonic()
        target = (
            evidence_root(store, identifier) / f"frame-{len(state['frames']) + 1}.jpg"
        )
        try:
            extract_frame(
                state["media"]["path"],
                timestamp,
                target,
                timeout=min(30, remaining),
                long_edge=long_edge,
            )
            state["frames"].append(
                {
                    "id": f"F{len(state['frames']) + 1:02d}",
                    "timestamp": timestamp,
                    "reason": reason.strip(),
                    "max_edge": long_edge,
                    "path": str(target),
                    "sha256": file_hash(target),
                    "fingerprint": str(image_fingerprint(target)),
                }
            )
        finally:
            run["elapsed"] += time.monotonic() - started
            store.save(run)
        return run


def refresh_native_captions(store, identifier):
    with store.lease(identifier):
        run = store.get(identifier)
        verify_bundle(store, run)
        state, settings = run["state"], run["settings"]
        remaining = settings["max_seconds"] - run["elapsed"]
        if remaining <= 0 or settings["local_media"]:
            raise ValueError(
                "Native refresh requires a network-sourced job with remaining time."
            )
        started = time.monotonic()
        directory = evidence_root(store, identifier) / "native"
        directory.mkdir(exist_ok=True)
        browser = (
            bilibili_browser(store.root)
            if settings["use_browser"] and run["platform"] == "bilibili"
            else nullcontext(None)
        )
        fetcher = None
        try:
            with browser as context:
                fetcher = VideoFetcher(
                    settings["max_bytes"],
                    started + remaining,
                    browser_cookie_jar(context) if context else None,
                )
                info = fetcher.metadata(settings["identity"])
                native = fetcher.captions(info, directory, run["platform"])
            if native is None:
                raise EvidencePending(
                    "The platform did not return usable native captions; the existing evidence is unchanged."
                )
            state.setdefault("caption_alternatives", []).append(native)
            if not state["assessments"]:
                state.setdefault("caption_history", []).append(state["captions"])
                state["captions"] = native
                cues = json.loads(Path(native["path"]).read_text(encoding="utf-8"))
                compact = compact_captions(cues, settings["caption_character_limit"])
                target = directory / "caption-excerpts.json"
                atomic_json(target, compact)
                state["caption_excerpt"] = {
                    "path": str(target),
                    "sha256": file_hash(target),
                    "selected_cues": len(compact),
                    "total_cues": len(cues),
                    "characters": sum(len(c["text"]) for c in compact),
                    "temporal_gaps": len(compact) < len(cues),
                }
                run["reason"] = (
                    "Native captions replaced the unassessed ASR input; media and frames were reused."
                )
            else:
                run["reason"] = (
                    "Native captions saved as supplementary evidence; existing assessed input was preserved."
                )
            state["resources"]["caption_download_bytes"] += native["download_bytes"]
        finally:
            try:
                if fetcher is not None:
                    fetcher.close()
            finally:
                elapsed = time.monotonic() - started
                run["elapsed"] += elapsed
                state["resources"]["preparation_seconds"] += elapsed
                store.save(run)
        return run
