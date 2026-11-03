import time

from broader_vids.creators import excluded
from broader_vids.evidence.media import public_error
from broader_vids.learning.inference import work_scope
from broader_vids.learning.service import ensure_model
from broader_vids.recommendations.learning_snapshot import round_learning
from broader_vids.preferences import get_profile, video_states
from broader_vids.recommendations.quality import cached_quality
from broader_vids.recommendations.ranking import rank_slate
from broader_vids.storage import utc_now


def update_preview(store, run, seconds=5):
    state, settings = run["state"], run["settings"]
    state["preview"] = []
    state["preview_generated_at"] = utc_now()
    deadline = time.monotonic() + seconds

    def check():
        if time.monotonic() >= deadline:
            raise ValueError("The provisional preview time allowance expired.")

    try:
        with work_scope(check):
            if get_profile(store)["revision"] != settings["profile"]["revision"]:
                raise ValueError("Interests changed after this round began.")
            observations = {}
            for key, record in state["evidence"].items():
                check()
                if record.get("quality") is not None:
                    verified = cached_quality(store, record["evidence_run"])
                    if verified and verified.get("canonical_id", key) == key:
                        observations[key] = verified
            if not observations:
                return
            states = video_states(store, run["platform"])
            items = [
                i
                for i in state["shortlist"]
                if not states.get(i["canonical_id"], {}).get("watched")
                and not states.get(i["canonical_id"], {}).get("dismissed")
                and not excluded(i, state.get("creator_rules"))
            ]
            learning = round_learning(store, run)
            if learning is None:
                learning = ensure_model(store)
            state["preview"] = rank_slate(
                items,
                observations,
                settings["profile"],
                settings["weights"],
                learning=learning,
                creator_rules=state.get("creator_rules"),
                policy_version=settings.get("screening_policy_version", 2),
            )
            state["preview_learning_snapshot"] = learning["id"]
            state.pop("preview_error", None)
    except Exception as error:
        state["preview_error"] = public_error(error)
