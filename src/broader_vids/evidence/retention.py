from broader_vids.learning.topic_labels import readable_card_topics
import copy
import json
from pathlib import Path

from broader_vids.storage import utc_now


def released_media(state):
    record, receipt = state.get("media"), state.get("media_retention", {})
    return bool(
        record
        and receipt.get("status") in {"release_pending", "released"}
        and receipt.get("media_sha256") == record.get("sha256")
        and receipt.get("media_bytes") == record.get("bytes")
    )


def owned_file(store, identifier, path):
    root = store.root / "evidence" / identifier
    candidate = Path(path).absolute()
    if (
        not candidate.is_relative_to(root)
        or not root.resolve().is_relative_to(store.root.resolve())
        or not candidate.resolve().is_relative_to(root.resolve())
    ):
        raise ValueError(
            "Cleanup can remove only files inside their evidence directory."
        )
    for part in (candidate, *candidate.parents):
        if part == store.root:
            break
        if part.is_symlink() or part.is_junction():
            raise ValueError("Cleanup cannot follow symbolic links or junctions.")
    return candidate


def discard_failed_media(store, identifier, *, lease_owner=None):
    with store.lease(identifier, owner=lease_owner):
        return _discard_failed_media(store, store.get(identifier))


def _discard_failed_media(store, run, *, dry_run=False):
    from broader_vids.evidence.failures import terminal_media_failure

    state, identifier = run["state"], run["id"]
    if (
        run["settings"].get("kind") != "evidence"
        or state.get("media")
        or not terminal_media_failure(state.get("failure", {}))
    ):
        return None
    root = store.root / "evidence" / identifier
    files = [owned_file(store, identifier, path) for path in root.glob("media.*")]
    files = [(path, path.stat().st_size) for path in files if path.is_file()]
    result = {
        "run_id": identifier,
        "status": "would_discard" if dry_run else "discarded",
        "files": len(files),
        "bytes": sum(size for _, size in files),
    }
    if dry_run or (
        not files and state.get("partial_media_cleanup", {}).get("status") != "pending"
    ):
        return result
    removed, removed_bytes = 0, 0
    cleanup_failed = False
    try:
        for path, size in files:
            path.unlink()
            removed += 1
            removed_bytes += size
    except OSError as error:
        cleanup_failed = True
        state["cleanup_issue"] = type(error).__name__
        result.update(status="partial", reason=type(error).__name__)
    if not cleanup_failed:
        state.pop("cleanup_issue", None)
    state["partial_media_cleanup"] = {
        "removed_bytes": state.get("partial_media_cleanup", {}).get("removed_bytes", 0)
        + removed_bytes,
        "status": "pending" if cleanup_failed else "removed",
        "updated_at": utc_now(),
    }
    store.save(run)
    return {**result, "files": removed, "bytes": removed_bytes}


def _validated_augmented(store, run):
    from broader_vids.evidence.assessment import validate_card
    from broader_vids.evidence.service import verify_bundle
    from broader_vids.recommendations.originality import context_identity

    state = run["state"]
    assessment = state.get("assessments", {}).get("augmented")
    if not assessment:
        return False
    verify_bundle(store, run)
    frames = state["frames"]
    identity = {
        "media_sha256": state["media"]["sha256"],
        "caption_sha256": state["caption_excerpt"]["sha256"],
        "frame_sha256": [f["sha256"] for f in frames],
        **context_identity(run["settings"].get("originality_context")),
    }
    if assessment["evidence_identity"] != identity:
        raise ValueError("Cleanup requires a scorecard bound to the retained evidence.")
    cues = json.loads(
        Path(state["caption_excerpt"]["path"]).read_text(encoding="utf-8")
    )
    signals = (run["settings"].get("originality_context") or {}).get("signals", [])
    validate_card(
        readable_card_topics(assessment["card"]),
        {cue["id"] for cue in cues}
        | {frame["id"] for frame in frames}
        | {signal["id"] for signal in signals},
        "augmented",
        assessment["rubric_version"],
    )
    return True


def compact_evidence(store, identifier, *, dry_run=False, lease_owner=None):
    from broader_vids.evidence.service import _require_current_rubric

    with store.lease(identifier, owner=lease_owner):
        run = store.get(identifier)
        if run["settings"].get("kind") != "evidence":
            raise ValueError("Cleanup requires an evidence run.")
        _require_current_rubric(run["settings"])
        discarded = _discard_failed_media(store, run, dry_run=dry_run)
        if discarded is not None:
            return discarded
        if not _validated_augmented(store, run):
            return {
                "run_id": identifier,
                "status": "retained",
                "reason": "unfinished_assessment",
                "files": 0,
                "bytes": 0,
            }
        state = run["state"]
        media = owned_file(store, identifier, state["media"]["path"])
        root = store.root / "evidence" / identifier
        protected = {
            Path(record["path"]).absolute()
            for record in [
                state["captions"],
                state["caption_excerpt"],
                *state["frames"],
                *state.get("caption_history", []),
                *state.get("caption_alternatives", []),
            ]
        }
        candidates = [media, *root.glob("media.*"), *root.glob("*.tmp")]
        files = []
        for path in dict.fromkeys(candidates):
            path = owned_file(store, identifier, path)
            if path not in protected and path.is_file():
                files.append((path, path.stat().st_size))
        result = {
            "run_id": identifier,
            "status": "would_compact" if dry_run else "compacted",
            "files": len(files),
            "bytes": sum(size for _, size in files),
        }
        if dry_run:
            return result
        previous = state.get("media_retention", {})
        if previous.get("status") == "released" and not files:
            return result
        state["media_retention"] = {
            "status": "release_pending",
            "media_sha256": state["media"]["sha256"],
            "media_bytes": state["media"]["bytes"],
            "verified_at": previous.get("verified_at", utc_now()),
        }
        store.save(run)
        removed, removed_bytes = 0, 0
        try:
            for path, size in files:
                path.unlink(missing_ok=True)
                removed += 1
                removed_bytes += size
        except OSError as error:
            state["media_retention"]["last_error"] = type(error).__name__
            store.save(run)
            raise
        state["media_retention"].update(status="released", released_at=utc_now())
        state.pop("cleanup_issue", None)
        store.save(run)
        result.update(files=removed, bytes=removed_bytes)
        return result


def _inherit_asset(store, identifier, source, destination, expected_hash):
    import os
    import shutil
    import uuid
    from broader_vids.evidence.media import file_hash

    destination = owned_file(store, identifier, destination)
    if destination.is_file() and file_hash(destination) == expected_hash:
        return
    temporary = owned_file(
        store, identifier, destination.with_name(f".inherit-{uuid.uuid4().hex}.tmp")
    )
    try:
        try:
            os.link(source, temporary)
        except OSError:
            shutil.copyfile(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def inherit_bundle(store, source, target, *, source_owner=None):
    from broader_vids.evidence.service import evidence_root, verify_bundle

    with store.lease(source["id"], owner=source_owner), store.lease(target["id"]):
        source = store.get(source["id"])
        verify_bundle(store, source)
        linked = {}
        target_root = evidence_root(store, target["id"])
        for temporary in target_root.glob(".inherit-*.tmp"):
            temporary = owned_file(store, target["id"], temporary)
            if temporary.is_file():
                temporary.unlink()
        for key in (
            "metadata",
            "media",
            "media_retention",
            "captions",
            "frames",
            "frame_positions_done",
            "frame_fractions",
            "caption_excerpt",
        ):
            if key not in source["state"]:
                continue
            value = copy.deepcopy(source["state"][key])
            for record in value if isinstance(value, list) else [value]:
                if not isinstance(record, dict) or not record.get("path"):
                    continue
                path = Path(record["path"])
                if str(path) not in linked:
                    destination = target_root / path.name
                    if key != "media" or not released_media(source["state"]):
                        _inherit_asset(
                            store, target["id"], path, destination, record["sha256"]
                        )
                    linked[str(path)] = str(destination)
                record["path"] = linked[str(path)]
            target["state"][key] = value
        target["state"]["inherited_from"] = source["id"]
        target["state"]["phase"] = "prepared"
        target["status"] = "prepared"
        store.save(target)
