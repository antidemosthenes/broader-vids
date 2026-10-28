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
        assessment["card"],
        {cue["id"] for cue in cues}
        | {frame["id"] for frame in frames}
        | {signal["id"] for signal in signals},
        "augmented",
        assessment["rubric_version"],
    )
    return True


def compact_evidence(store, identifier, *, dry_run=False, lease_owner=None):
    with store.lease(identifier, owner=lease_owner):
        run = store.get(identifier)
        if run["settings"].get("kind") != "evidence":
            raise ValueError("Cleanup requires an evidence run.")
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


def inherit_bundle(store, source, target, *, source_owner=None):
    import os
    import shutil
    from broader_vids.evidence.service import evidence_root, verify_bundle

    with store.lease(source["id"], owner=source_owner), store.lease(target["id"]):
        source = store.get(source["id"])
        verify_bundle(store, source)
        linked = {}
        target_root = evidence_root(store, target["id"])
        for key in (
            "metadata",
            "media",
            "media_retention",
            "captions",
            "frames",
            "frame_positions_done",
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
                        try:
                            os.link(path, destination)
                        except OSError:
                            shutil.copyfile(path, destination)
                    linked[str(path)] = str(destination)
                record["path"] = linked[str(path)]
            target["state"][key] = value
        target["state"]["inherited_from"] = source["id"]
        target["state"]["phase"] = "prepared"
        target["status"] = "prepared"
        store.save(target)
