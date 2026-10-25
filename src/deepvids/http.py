import json
import time
import urllib.error
import urllib.parse
import urllib.request


class AccessPaused(Exception):
    pass


class BudgetExhausted(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise AccessPaused("Platform redirected the request; access configuration needs review.")


class PublicHttp:
    def __init__(self, store, run, clock=time.monotonic, sleeper=time.sleep):
        self.store = store
        self.run = run
        self.clock = clock
        self.sleeper = sleeper
        self.started = clock()
        self.initial_elapsed = run["elapsed"]
        self.last_request = None

    def elapsed(self):
        return self.initial_elapsed + self.clock() - self.started

    def check(self):
        if self.elapsed() >= self.run["settings"]["max_seconds"]:
            raise BudgetExhausted("Session time budget exhausted.")
        if self.store.request_count(self.run["id"]) >= self.run["settings"]["max_requests"]:
            raise BudgetExhausted("Session HTTP request budget exhausted.")

    def get(self, url):
        return self.request(url)

    def post(self, url, payload):
        return self.request(url, json.dumps(payload).encode("utf-8"))

    def request(self, url, data=None):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in {"www.youtube.com", "api.bilibili.com"}:
            raise ValueError("Only the declared public platform HTTPS endpoints are supported.")
        self.check()
        if self.last_request is not None:
            self.sleeper(max(0, 1.0 - (self.clock() - self.last_request)))
        self.check()
        request_id = self.store.begin_request(self.run["id"], url)
        self.last_request = self.clock()
        headers = {"User-Agent": "Deepvids/0.1 sampling-feasibility", "Accept-Encoding": "identity"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            timeout = min(20.0, self.run["settings"]["max_seconds"] - self.elapsed())
            if timeout <= 0:
                raise BudgetExhausted("Session time budget exhausted.")
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
                final = urllib.parse.urlsplit(response.geturl())
                if final.hostname != parsed.hostname:
                    raise AccessPaused("Platform redirected to another host; access configuration needs review.")
                body = response.read(4 * 1024 * 1024 + 1)
                if len(body) > 4 * 1024 * 1024:
                    raise AccessPaused("Response exceeded the 4 MiB metadata limit.")
            self.store.end_request(request_id, "ok", f"bytes:{len(body)}")
            return body.decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeError) as error:
            detail = f"HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else type(error).__name__
            self.store.end_request(request_id, "unresolved", detail)
            raise AccessPaused(f"Public request failed ({detail}); no conclusion about video existence was made.") from None
        except (AccessPaused, BudgetExhausted) as error:
            self.store.end_request(request_id, "unresolved", str(error))
            raise

    def get_json(self, url):
        return self.decode_json(self.get(url))

    def post_json(self, url, payload):
        return self.decode_json(self.post(url, payload))

    @staticmethod
    def decode_json(body):
        try:
            result = json.loads(body)
        except json.JSONDecodeError:
            raise AccessPaused("Expected JSON metadata; received an unrecognized response.") from None
        if not isinstance(result, dict):
            raise AccessPaused("Expected a JSON object; received an unrecognized response.")
        return result
