import random
import string

from .http import AccessPaused, BudgetExhausted, PublicHttp
from .models import Archive, Outcome
from .providers import Bilibili, MAX_AID, YouTube, prefix_matches
from .sampling import Kernel, restore_rng, step_from_pending


def create_experiment(store, platform, seed=0, max_requests=1000, max_seconds=3600,
                      queries=100, steps=250, seed_aids=(), seed_provenance=""):
    if platform not in {"youtube", "bilibili"}:
        raise ValueError("Unsupported platform.")
    if not 1 <= max_requests <= 1000 or not 0 < max_seconds <= 3600:
        raise ValueError("Sessions require 1–1,000 requests and at most 3,600 seconds.")
    if not 1 <= queries <= 1000 or not 16 <= steps <= 10000:
        raise ValueError("Use 1–1,000 prefix queries and 16–10,000 steps per chain.")
    if platform == "bilibili":
        if len(seed_aids) != 4 or len(set(seed_aids)) != 4 or any(type(a) is not int or not 1 <= a <= MAX_AID for a in seed_aids):
            raise ValueError("Bilibili experiments require four distinct verified starting AIDs in the declared domain.")
        if not seed_provenance.strip():
            raise ValueError("Record the provenance and limitations of the four starting archives.")
    settings = {"seed": seed, "max_requests": max_requests, "max_seconds": max_seconds,
                "queries": queries, "steps_per_chain": steps, "seed_aids": list(seed_aids),
                "seed_provenance": seed_provenance, "kind": "experiment", "sampler_version": 1}
    rng = random.Random(seed)
    state = {"phase": "bootstrap", "cache": {}}
    if platform == "youtube":
        state.update(prefixes=["".join(rng.choice(string.ascii_lowercase) for _ in range(5)) for _ in range(queries)],
                     query_index=0, candidates=[], metadata_index=0)
    else:
        state.update(chains=[], seed_index=0, next_chain=0,
                     chain_seeds=[rng.getrandbits(64) for _ in range(4)])
    return store.create(platform, settings, state)


def create_probe(store, platform, identifier):
    return store.create(platform, {"kind": "probe", "identifier": identifier, "max_requests": 1,
                                   "max_seconds": 30, "sampler_version": 1}, {"phase": "probe", "cache": {}})


def create_search_control(store, video_id):
    return store.create("youtube", {"kind": "search_control", "identifier": video_id, "max_requests": 2,
                                    "max_seconds": 30, "sampler_version": 1}, {"phase": "bootstrap", "cache": {}})


def execute(store, run_id, http_factory=PublicHttp, youtube_factory=YouTube, bili_factory=Bilibili):
    with store.lease(run_id):
        run = store.get(run_id)
        if run["status"] in {"completed", "budget_exhausted"}:
            return run
        http = http_factory(store, run)
        run.update(status="running", reason="")
        store.save(run)

        def save(kind=None, payload=None, additional=()):
            run["elapsed"] = http.elapsed()
            store.save(run, (kind, payload) if kind else None, additional)

        try:
            provider = youtube_factory(http) if run["platform"] == "youtube" else bili_factory(http)
            if run["settings"]["kind"] == "search_control":
                state = run["state"]
                if state["phase"] == "bootstrap":
                    state["client_version"] = provider.bootstrap()
                    state["phase"] = "search"
                    save()
                page = provider.search(run["settings"]["identifier"], state["client_version"])
                save("search_control", {"requested_id": run["settings"]["identifier"],
                                        "requested_id_found": run["settings"]["identifier"] in page.video_ids,
                                        "returned_id_count": len(page.video_ids),
                                        "continuation_present": page.continuation_present})
                state["phase"] = "done"
            elif run["settings"]["kind"] == "probe":
                result = provider.resolve(run["settings"]["identifier"])
                if result.outcome == Outcome.UNRESOLVED:
                    save("probe", {"outcome": result.outcome, "reason": result.reason, "archive": None})
                    raise AccessPaused(result.reason)
                save("probe", {"outcome": result.outcome, "reason": result.reason,
                               "archive": result.archive.to_dict() if result.archive else None})
            elif run["platform"] == "youtube":
                _youtube(run, provider, save)
            else:
                _bilibili(run, provider, save, http)
            run.update(status="completed", reason="The declared experiment completed; coverage validation is separate.")
        except BudgetExhausted as error:
            run.update(status="budget_exhausted", reason=str(error))
        except AccessPaused as error:
            run.update(status="paused", reason=str(error))
        except KeyboardInterrupt:
            run.update(status="paused", reason="Interrupted; continue this run to retry its saved pending work.")
        except Exception:
            run["state"] = store.get(run_id)["state"]
            run.update(status="paused", reason="Unexpected failure; pending work is saved for diagnosis.")
            raise
        finally:
            save()
        return store.get(run_id)


def _youtube(run, provider, save):
    state = run["state"]
    if state["phase"] == "bootstrap":
        state["client_version"] = provider.bootstrap()
        state["phase"] = "queries"
        save()
    while state["query_index"] < len(state["prefixes"]):
        prefix = state["prefixes"][state["query_index"]]
        page = provider.search(prefix, state["client_version"])
        if not page.recognized:
            raise AccessPaused("Unrecognized search schema; the query remains pending.")
        matching = [v for v in page.video_ids if prefix_matches(v, prefix)]
        known = set(state["candidates"])
        for video_id in matching:
            if video_id not in known:
                state["candidates"].append(video_id)
                known.add(video_id)
        state["query_index"] += 1
        save("prefix", {"prefix": prefix, "matching_ids": matching, "returned_id_count": len(page.video_ids),
                        "continuation_present": page.continuation_present, "query_index": state["query_index"]})
    if state["phase"] == "queries":
        random.Random(run["settings"]["seed"] ^ 0xD33F).shuffle(state["candidates"])
        state["phase"] = "metadata"
        save()
    while state["metadata_index"] < len(state["candidates"]):
        video_id = state["candidates"][state["metadata_index"]]
        result = provider.resolve(video_id)
        if result.outcome == Outcome.UNRESOLVED:
            save("resolution", {"identifier": video_id, "outcome": result.outcome, "reason": result.reason, "archive": None})
            raise AccessPaused(result.reason)
        state["metadata_index"] += 1
        state["cache"][video_id] = result.archive.to_dict() if result.archive else None
        additional = []
        if result.archive:
            part = result.archive.parts[0]
            additional.append(("sample", _sample(result.archive, part, None, state["metadata_index"], False)))
        save("resolution", {"identifier": video_id, "outcome": result.outcome, "reason": result.reason,
                            "archive": result.archive.to_dict() if result.archive else None}, additional)
    state["phase"] = "done"


def _sample(archive, part, chain, step, warmup):
    return {"archive_id": archive.archive_id, "part_key": part.key,
            "duration_seconds": part.duration_seconds, "published_at": archive.published_at,
            "views": archive.views, "part_count": len(archive.parts),
            "access_evidence": archive.access_evidence, "chain": chain, "step": step, "warmup": warmup}


def _bilibili(run, provider, save, http):
    state = run["state"]
    settings = run["settings"]
    while state["seed_index"] < 4:
        index = state["seed_index"]
        aid = settings["seed_aids"][index]
        result = provider.resolve(aid)
        if result.outcome != Outcome.VALID:
            save("resolution", {"identifier": aid, "outcome": result.outcome, "reason": result.reason, "archive": None})
            raise AccessPaused(result.reason or f"Starting AID {aid} could not be verified.")
        archive = result.archive
        state["cache"][str(aid)] = archive.to_dict()
        state["chains"].append({"archive": archive.to_dict(), "step": 0, "pending": None,
                                "rng": random.Random(state["chain_seeds"][index]).getstate(),
                                "part_rng": random.Random(state["chain_seeds"][index] ^ 0xFA47).getstate()})
        state["seed_index"] += 1
        save("seed", {"chain": index, "archive": archive.to_dict(), "provenance": settings["seed_provenance"]})
    state["phase"] = "chains"
    kernel = Kernel()
    while any(c["step"] < settings["steps_per_chain"] for c in state["chains"]):
        if http.elapsed() >= settings["max_seconds"]:
            raise BudgetExhausted("Session time budget exhausted.")
        index = state["next_chain"]
        chain = state["chains"][index]
        if chain["step"] >= settings["steps_per_chain"]:
            state["next_chain"] = (index + 1) % 4
            continue
        previous = int(chain["archive"]["archive_id"])
        if chain["pending"] is None:
            rng = restore_rng(chain["rng"])
            chain["pending"] = {"aid": kernel.propose(previous, rng), "uniform": rng.random()}
            chain["rng"] = rng.getstate()
            save()
        proposed = chain["pending"]["aid"]
        outcome = Outcome.UNAVAILABLE
        archive = None
        if 1 <= proposed <= MAX_AID:
            if str(proposed) not in state["cache"]:
                result = provider.resolve(proposed)
                if result.outcome == Outcome.UNRESOLVED:
                    save("resolution", {"identifier": proposed, "outcome": result.outcome, "reason": result.reason, "archive": None})
                    raise AccessPaused(result.reason)
                state["cache"][str(proposed)] = result.archive.to_dict() if result.archive else None
                save("resolution", {"identifier": proposed, "outcome": result.outcome, "reason": result.reason,
                                    "archive": result.archive.to_dict() if result.archive else None})
            cached = state["cache"][str(proposed)]
            if cached is not None:
                archive = Archive.from_dict(cached)
                outcome = Outcome.VALID
        accepted = step_from_pending(chain, archive)
        current = Archive.from_dict(chain["archive"])
        part_rng = restore_rng(chain["part_rng"])
        part = part_rng.choice(current.parts)
        chain["part_rng"] = part_rng.getstate()
        event = _sample(current, part, index, chain["step"], chain["step"] <= settings["steps_per_chain"] // 4)
        event.update(proposed_aid=proposed, outcome=outcome, accepted=accepted,
                     moved=int(current.archive_id) != previous)
        state["next_chain"] = (index + 1) % 4
        save("sample", event)
    state["phase"] = "done"

