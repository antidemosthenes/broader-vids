from importlib.resources import files
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import sqlite3

from deepvids.evidence.assessment import current_card
from deepvids.preferences import get_profile, user_action, video_states
from deepvids.storage import Store
from deepvids.recommendations.originality import provenance


PAGE = files("deepvids").joinpath("web/static/index.html").read_text(encoding="utf-8")


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
    runs = {}
    current = []
    visible = {
        r[0]
        for r in store.db.execute(
            "SELECT canonical_id FROM video_state WHERE recommended=1 AND watched=0 AND dismissed=0"
        )
    }
    for platform in ("youtube", "bilibili"):
        query = "SELECT id FROM runs WHERE json_extract(settings,'$.kind')='recommendation' AND platform=?"
        row = store.db.execute(
            query
            + " AND status IN ('completed','partial') ORDER BY created_at DESC LIMIT 1",
            (platform,),
        ).fetchone()
        row = (
            row
            or store.db.execute(
                query + " ORDER BY created_at DESC LIMIT 1", (platform,)
            ).fetchone()
        )
        if row:
            run = store.get(row[0])
            current.append(run)
            visible.update(i["canonical_id"] for i in run["state"]["selected"])
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
                "selected": [
                    {
                        **score_details(store, item),
                        "originality_evidence": provenance(
                            {
                                **item,
                                "engagement": snapshots.get(
                                    item["canonical_id"], item.get("engagement", {})
                                ),
                            }
                        ),
                    }
                    for item in state["selected"]
                ],
                "weights": run["settings"].get("weights", {}),
                "assessed": sum(
                    v.get("quality") is not None for v in state["evidence"].values()
                ),
                "processed": sum(
                    v.get("quality") is not None or v.get("status") == "unavailable"
                    for v in state["evidence"].values()
                ),
                "inspection": len(state.get("shortlist", [])) or 50,
                "unavailable": sum(
                    v.get("status") == "unavailable" for v in state["evidence"].values()
                ),
                "issues": len(state["issues"]),
            }
            if state.get("published_at"):
                runs[run["platform"]] = entry
            else:
                if run["platform"] not in runs or not runs[run["platform"]]["selected"]:
                    runs[run["platform"]] = entry
    states = video_states(store, identifiers=list(visible))
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
                field: v[field]
                for field in ("watched", "dismissed", "pending", "feedback")
            }
            for k, v in states.items()
        },
        "pending": {p: len(items) for p, items in pending.items()},
    }


def make_server(root, port=8765):
    token = secrets.token_urlsafe(32)

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
            if self.path == "/":
                return self.send(200, PAGE.replace("__TOKEN__", token), "text/html")
            assets = {
                "/static/dashboard.js": ("dashboard.js", "text/javascript"),
                "/static/dashboard.css": ("dashboard.css", "text/css"),
            }
            if self.path in assets:
                name, mime = assets[self.path]
                return self.send(
                    200,
                    files("deepvids")
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
            if (
                not self.valid_host()
                or self.headers.get("X-Deepvids-Token") != token
                or self.headers.get("Origin") not in origins | {None}
            ):
                return self.send(
                    403, '{"error":"Invalid local action token or origin"}'
                )
            if self.path != "/api/action":
                return self.send(404, '{"error":"Not found"}')
            store = None
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise ValueError("Action body must be 1–8,192 bytes.")
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict) or not all(
                    isinstance(value.get(k), str) for k in ("id", "action")
                ):
                    raise ValueError("An action needs a video ID and action name.")
                store = Store(root)
                user_action(
                    store,
                    value["id"],
                    value["action"],
                    value.get("reason", ""),
                    value.get("kind", "overall"),
                )
                self.send(200, '{"saved":true}')
            except (ValueError, OSError, sqlite3.Error) as error:
                self.send(400, json.dumps({"error": str(error)}))
            finally:
                if store:
                    store.close()

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
