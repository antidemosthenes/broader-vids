from broader_vids.recommendations.screening import inspection_limit
from importlib.resources import files
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import sqlite3
import threading
import socket
import logging
from urllib.parse import urlsplit, parse_qs

from broader_vids import __version__

from broader_vids.evidence.assessment import current_card
from broader_vids.evidence.media import EvidencePending
from broader_vids.preferences import (
    get_profile,
    set_profile,
    ProfileConflict,
    user_action,
    video_states,
    current_recommendation_id,
)
from broader_vids.storage import Store
from broader_vids.tag_rules import tag_rules
from broader_vids.recommendations.originality import provenance
from broader_vids.creators import (
    status as exclusion_status,
    video_creator,
    checkbox_state,
    set_exclusion,
)


PAGE = (
    files("broader_vids").joinpath("web/static/index.html").read_text(encoding="utf-8")
)


def score_details(store, item):
    result = dict(item)
    evidence = item.get("quality_evidence", {})
    result["quality_breakdown"] = None
    if not evidence.get("evidence_run"):
        return result
    try:
        run = store.get(evidence["evidence_run"])
    except ValueError:
        return result
    assessment = run["state"].get("assessments", {}).get(evidence.get("mode"), {})
    card = assessment.get("card", {})
    if not card or assessment.get("overall_quality") != evidence.get("quality"):
        return result
    card = current_card(card)
    result["quality_evidence"] = {
        **evidence,
        "summary": card["summary"],
        "unknowns": card["unknowns"],
    }
    result["quality_breakdown"] = {
        "content_quality": card["content_quality"],
        "duration_prior": assessment["duration_prior"],
        "criteria": card["criteria"],
        "rationale": card["overall_rationale"],
    }
    return result


def dashboard_state(store):
    from broader_vids.learning.feedback import details, context_from_features
    from broader_vids.learning.features import capture
    from broader_vids.learning.annotations import latest_annotations, apply_annotation

    annotations = latest_annotations(store)
    exclusions = exclusion_status(store)

    runs = {}
    profile = get_profile(store)
    current = []
    visible = {
        r[0]
        for r in store.db.execute(
            "SELECT canonical_id FROM video_state WHERE recommended=1 AND watched=0 AND dismissed=0"
        )
    }
    for platform in ("youtube", "bilibili"):
        identifier = current_recommendation_id(store, platform)
        if identifier:
            run = store.get(identifier)
            current.append(run)
            visible.update(
                i["canonical_id"]
                for i in (
                    run["state"].get("selected") or run["state"].get("preview", [])
                )
            )
    snapshots = {
        r["canonical_id"]: json.loads(r["payload"])
        for r in store.db.execute(
            "SELECT canonical_id,payload FROM engagement_snapshots WHERE canonical_id IN ("
            + ",".join("?" for _ in visible)
            + ")",
            list(visible),
        )
    }
    for run in current:
        if run["settings"].get("kind") == "recommendation":
            state = run["state"]
            entry = {
                "id": run["id"],
                "status": run["status"],
                "provisional": not bool(state.get("published_at")),
                "selected": [
                    {
                        **score_details(store, item),
                        "originality_evidence": provenance(
                            {
                                **item,
                                "engagement": snapshots.get(
                                    item["canonical_id"], item.get("engagement", {})
                                ),
                            },
                            profile,
                        ),
                    }
                    for item in (state.get("selected") or state.get("preview", []))
                ],
                "weights": run["settings"].get("weights", {}),
                "assessed": sum(
                    v.get("quality") is not None for v in state["evidence"].values()
                ),
                "processed": sum(
                    v.get("quality") is not None or v.get("status") == "unavailable"
                    for v in state["evidence"].values()
                ),
                "inspection": len(state.get("shortlist", []))
                or inspection_limit(run["settings"]),
                "unavailable": sum(
                    v.get("status") == "unavailable" for v in state["evidence"].values()
                ),
                "issues": len(state["issues"]),
            }
            runs[run["platform"]] = entry
    states = video_states(store, identifiers=list(visible))
    structured = details(store, list(visible))
    items = {key: value["payload"] for key, value in states.items()}
    items.update(
        {
            item["canonical_id"]: item
            for run in current
            for item in (
                run["state"].get("selected") or run["state"].get("preview", [])
            )
        }
    )
    contexts = {
        item["canonical_id"]: context_from_features(
            apply_annotation(capture(item), annotations.get(item["canonical_id"]))
        )
        for item in items.values()
    }
    pending = {
        p: [
            v["payload"] for v in states.values() if v["pending"] and v["platform"] == p
        ]
        for p in ("youtube", "bilibili")
    }
    return {
        "profile": get_profile(store),
        "runs": runs,
        "pending_items": pending,
        "videos": {
            k: {
                "details": structured.get(k, {}),
                "feedback_context": contexts.get(k, {}),
                "creator": checkbox_state(video_creator(store, k), exclusions),
                **{
                    field: v[field]
                    for field in (
                        "watched",
                        "dismissed",
                        "pending",
                    )
                },
            }
            for k in visible
            for v in [
                states.get(k, {"watched": False, "dismissed": False, "pending": False})
            ]
        },
        "pending": {p: len(items) for p, items in pending.items()},
    }


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def server_close(self):
        if hasattr(self, "account_switch"):
            self.account_switch.close()
        super().server_close()

    def handle_error(self, request, client_address):
        import sys

        if isinstance(
            sys.exc_info()[1],
            (BrokenPipeError, ConnectionResetError, ConnectionAbortedError),
        ):
            return
        logging.getLogger("broader_vids.web").exception("Dashboard request failed.")


def make_server(root, port=8765, *, control=None):
    token = secrets.token_urlsafe(32)
    from broader_vids.evidence.account_switch import AccountSwitch

    account_switch = AccountSwitch(root)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def valid_host(self):
            return self.headers.get("Host") in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }

        def send(self, status, body, mime="application/json"):
            raw = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self.valid_host():
                return self.send(403, '{"error":"Invalid local host"}')
            if self.path == "/api/health" and control:
                return self.send(
                    200,
                    json.dumps(
                        {
                            "instance_id": control["instance_id"],
                            "data_key": control["data_key"],
                            "version": __version__,
                        }
                    ),
                )
            if self.path == "/":
                return self.send(200, PAGE.replace("__TOKEN__", token), "text/html")
            if self.path == "/api/bilibili/account":
                return self.send(
                    200, json.dumps(account_switch.status(), ensure_ascii=False)
                )
            if urlsplit(self.path).path == "/api/token-usage":
                store = None
                try:
                    from broader_vids.token_usage import report

                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                    if set(query) - {"run_id"} or (
                        "run_id" in query and len(query["run_id"]) != 1
                    ):
                        raise ValueError("Select one recommendation round.")
                    store = Store(root)
                    return self.send(
                        200, json.dumps(report(store, query.get("run_id", [None])[0]))
                    )
                except (ValueError, OSError, sqlite3.Error) as error:
                    return self.send(400, json.dumps({"error": str(error)}))
                finally:
                    if store is not None:
                        store.close()
            if self.path == "/api/interests":
                store = Store(root)
                try:
                    return self.send(
                        200,
                        json.dumps(
                            get_profile(store) or {"revision": 0, "interests": []},
                            ensure_ascii=False,
                        ),
                    )
                finally:
                    store.close()
            if self.path == "/api/exclusions":
                store = Store(root)
                try:
                    return self.send(200, json.dumps(exclusion_status(store)))
                finally:
                    store.close()
            if self.path in {"/api/learning", "/api/calibration"}:
                from broader_vids.learning.service import learning_status
                from broader_vids.learning.calibration import calibration_list

                store = Store(root)
                try:
                    value = (
                        learning_status(store)
                        if self.path == "/api/learning"
                        else [
                            {
                                "item": r["item"],
                                "watched": r["watched"],
                                "details": r["details"],
                                "feedback_context": r["feedback_context"],
                                "creator": r["creator"],
                            }
                            for r in calibration_list(store, latest=True)
                        ]
                    )
                    return self.send(200, json.dumps(value, ensure_ascii=False))
                finally:
                    store.close()
            assets = {
                "/static/dashboard.js": ("dashboard.js", "text/javascript"),
                "/static/dashboard.css": ("dashboard.css", "text/css"),
                "/static/api.js": ("api.js", "text/javascript"),
                "/static/state.js": ("state.js", "text/javascript"),
                "/static/ui.js": ("ui.js", "text/javascript"),
                "/static/review.js": ("review.js", "text/javascript"),
                "/static/views.js": ("views.js", "text/javascript"),
                "/static/score.js": ("score.js", "text/javascript"),
                "/static/preferences.js": ("preferences.js", "text/javascript"),
                "/static/token-usage.js": ("token-usage.js", "text/javascript"),
                "/static/bilibili-account.js": (
                    "bilibili-account.js",
                    "text/javascript",
                ),
            }
            if self.path in assets:
                name, mime = assets[self.path]
                return self.send(
                    200,
                    files("broader_vids")
                    .joinpath("web/static", name)
                    .read_text(encoding="utf-8"),
                    mime,
                )
            if self.path == "/api/state":
                store = Store(root)
                try:
                    return self.send(
                        200, json.dumps(dashboard_state(store), ensure_ascii=False)
                    )
                finally:
                    store.close()
            return self.send(404, '{"error":"Not found"}')

        def do_POST(self):
            origins = {
                f"http://127.0.0.1:{self.server.server_port}",
                f"http://localhost:{self.server.server_port}",
            }
            if self.path == "/api/shutdown" and control:
                if (
                    not self.valid_host()
                    or self.headers.get("Origin") not in origins | {None}
                    or not secrets.compare_digest(
                        self.headers.get("X-Broader-Vids-Control", "").encode("utf-8"),
                        control["stop_token"].encode("ascii"),
                    )
                ):
                    return self.send(
                        403, '{"error":"Invalid dashboard control token or origin"}'
                    )
                self.send(200, '{"stopping":true}')
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if (
                not self.valid_host()
                or self.headers.get("X-Broader-Vids-Token") != token
                or self.headers.get("Origin") not in origins | {None}
            ):
                return self.send(
                    403, '{"error":"Invalid local action token or origin"}'
                )
            if self.path not in {
                "/api/action",
                "/api/interests",
                "/api/preferences/export",
                "/api/preferences/preview",
                "/api/preferences/import",
                "/api/bilibili/account",
            }:
                return self.send(404, '{"error":"Not found"}')
            store = None
            try:
                length = int(self.headers.get("Content-Length", "0"))
                transfer = self.path.startswith("/api/preferences/")
                if (
                    not 0
                    < length
                    <= (
                        16 * 1024 * 1024
                        if transfer
                        else 32768
                        if self.path == "/api/interests"
                        else 8192
                    )
                ):
                    raise ValueError(
                        "Preference request must be 1–16 MiB."
                        if transfer
                        else "Interest request must be 1–32,768 bytes."
                        if self.path == "/api/interests"
                        else "Action body must be 1–8,192 bytes."
                    )
                value = json.loads(self.rfile.read(length))
                if self.path == "/api/interests":
                    if (
                        not isinstance(value, dict)
                        or set(value) != {"interests", "revision"}
                        or type(value["revision"]) is not int
                        or value["revision"] < 0
                    ):
                        raise ValueError(
                            "Load the saved interests before saving changes."
                        )
                    store = Store(root)
                    result = set_profile(
                        store, value["interests"], expected_revision=value["revision"]
                    )
                    return self.send(200, json.dumps(result, ensure_ascii=False))
                if self.path == "/api/bilibili/account":
                    if (
                        not isinstance(value, dict)
                        or not isinstance(value.get("operation"), str)
                        or value["operation"]
                        not in {
                            "start",
                            "confirm",
                            "cancel",
                        }
                    ):
                        raise ValueError(
                            "Use start, confirm, or cancel for Bilibili account switching."
                        )
                    operation = getattr(account_switch, value["operation"])
                    return self.send(200, json.dumps(operation(), ensure_ascii=False))
                if transfer:
                    from broader_vids.learning.transfer import (
                        export_preferences,
                        import_preferences,
                        parse_bundle,
                        preview_import,
                    )

                    if not isinstance(value, dict):
                        raise ValueError("Preference operations require a JSON object.")
                    store = Store(root)
                    if self.path == "/api/preferences/export":
                        result = {
                            "file_text": json.dumps(
                                export_preferences(store),
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                        }
                    else:
                        bundle = parse_bundle(value.get("file_text"))
                        if self.path == "/api/preferences/import" and not isinstance(
                            value.get("revision"), str
                        ):
                            raise ValueError(
                                "Preview the preference file before importing it."
                            )
                        result = (
                            import_preferences(
                                store,
                                bundle,
                                expected_revision=value.get("revision"),
                                _validated=True,
                            )
                            if self.path == "/api/preferences/import"
                            else preview_import(store, bundle, _validated=True)
                        )
                    if self.path == "/api/preferences/import":
                        result["profile"] = get_profile(store)
                    return self.send(200, json.dumps(result, ensure_ascii=False))
                if not isinstance(value, dict) or not all(
                    isinstance(value.get(k), str) for k in ("id", "action")
                ):
                    raise ValueError("An action needs a video ID and action name.")
                store = Store(root)
                if value["action"] == "creator_rule":
                    creator = video_creator(store, value["id"])
                    current = checkbox_state(creator, exclusion_status(store))
                    if current["imported"]:
                        raise ValueError(
                            "Unblock this creator on Bilibili, then sync its blacklist."
                        )
                    return self.send(
                        200,
                        json.dumps(
                            set_exclusion(
                                store,
                                creator["platform"],
                                creator["creator_id"],
                                value.get("excluded"),
                                creator["name"],
                            )
                        ),
                    )
                if value["action"] == "remove_creator":
                    return self.send(
                        200,
                        json.dumps(
                            set_exclusion(
                                store,
                                value.get("platform"),
                                value.get("creator_id"),
                                False,
                            )
                        ),
                    )
                if value["action"] == "sync_blacklist":
                    from broader_vids.blacklist import sync_blacklist

                    return self.send(200, json.dumps(sync_blacklist(store)))
                if value["action"] in {"add_tag", "remove_tag"}:
                    from broader_vids.preferences import update_tag_rule

                    update_tag_rule(
                        store,
                        value.get("platform"),
                        value.get("tag"),
                        remove=value["action"] == "remove_tag",
                    )
                    profile = get_profile(store)
                    return self.send(
                        200,
                        json.dumps(
                            {
                                **profile.get("excluded_tags", {}),
                                "tag_revision": profile["revision"],
                                "tags": {
                                    platform: tag_rules(profile, platform)
                                    for platform in ("youtube", "bilibili")
                                },
                            },
                            ensure_ascii=False,
                        ),
                    )
                if value["action"] == "rate":
                    from broader_vids.learning.feedback import save_detail

                    save_detail(
                        store,
                        value["id"],
                        value.get("kind"),
                        value.get("value"),
                        target=value.get("target", ""),
                        level=None
                        if value.get("kind") == "depth"
                        and value.get("level_source") == "ai"
                        else value.get("level"),
                    )
                else:
                    if value["action"] not in {
                        "watched",
                        "unwatched",
                        "dismiss",
                        "restore",
                    }:
                        raise ValueError(
                            "Use the separate topic, depth, and quality fields instead of binary votes."
                        )
                    user_action(
                        store,
                        value["id"],
                        value["action"],
                    )
                self.send(200, '{"saved":true}')
            except ProfileConflict as error:
                self.send(409, json.dumps({"error": str(error)}))
            except (ValueError, OSError, sqlite3.Error, EvidencePending) as error:
                self.send(400, json.dumps({"error": str(error)}))
            finally:
                if store:
                    store.close()

    server = LocalServer(("127.0.0.1", port), Handler)
    server.account_switch = account_switch
    return server
