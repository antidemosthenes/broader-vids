import json
import gzip
import hashlib
import http.cookiejar
import io
import time
import urllib.error
import urllib.parse
import urllib.request
import threading


class RequestGate:
    def __init__(self):
        self.lock = threading.Lock()
        self.last_request = None


class AccessPaused(Exception):
    pass


class BudgetExhausted(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise AccessPaused(
            "Platform redirected the request; access configuration needs review."
        )


class PublicHttp:
    def __init__(
        self,
        store,
        run,
        clock=time.monotonic,
        sleeper=time.sleep,
        anonymous_session=False,
    ):
        self.store = store
        self.run = run
        self.clock = clock
        self.sleeper = sleeper
        self.started = clock()
        self.initial_elapsed = run["elapsed"]
        self.last_request = None
        self.anonymous_session = anonymous_session
        self.cookies = http.cookiejar.CookieJar() if anonymous_session else None
        self.opener = urllib.request.build_opener(
            NoRedirect(),
            *(
                [urllib.request.HTTPCookieProcessor(self.cookies)]
                if anonymous_session
                else []
            ),
        )
        self.last_receipt = None
        self.transport = "anonymous_http" if anonymous_session else "public_http"

    def elapsed(self):
        return self.initial_elapsed + self.clock() - self.started

    def check(self):
        if getattr(self, "cancelled", None) is not None and self.cancelled.is_set():
            raise BudgetExhausted("The parent cancelled platform retrieval.")
        if self.elapsed() >= self.run["settings"]["max_seconds"]:
            raise BudgetExhausted("Session time budget exhausted.")
        if (
            self.store.request_count(self.run["id"])
            >= self.run["settings"]["max_requests"]
        ):
            raise BudgetExhausted("Session HTTP request budget exhausted.")

    def get(self, url):
        return self.request(url)

    def post(self, url, payload):
        return self.request(url, json.dumps(payload).encode("utf-8"))

    def request(self, url, data=None):
        parsed = urllib.parse.urlsplit(url)
        hosts = {"www.youtube.com", "api.bilibili.com"}
        if self.anonymous_session:
            hosts |= {"www.bilibili.com", "search.bilibili.com", "space.bilibili.com"}
        if (
            parsed.scheme != "https"
            or parsed.hostname not in hosts
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise ValueError(
                "Only the declared public platform HTTPS endpoints are supported."
            )
        gate = getattr(self, "request_gate", None)
        if gate is not None:
            with gate.lock:
                self.check()
                if gate.last_request is not None:
                    self.sleeper(max(0, 1.0 - (self.clock() - gate.last_request)))
                self.check()
                request_id = self.store.begin_request(self.run["id"], url)
                gate.last_request = self.clock()
        else:
            self.check()
            if self.last_request is not None:
                self.sleeper(max(0, 1.0 - (self.clock() - self.last_request)))
            self.check()
            request_id = self.store.begin_request(self.run["id"], url)
        self.last_receipt = None
        self.last_request = self.clock()
        headers = {
            "User-Agent": "Broader Vids/0.1 sampling-feasibility",
            "Accept-Encoding": "identity",
        }
        if self.anonymous_session:
            headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
                    "Referer": "https://www.bilibili.com/"
                    if "bilibili.com" in parsed.hostname
                    else "https://www.youtube.com/",
                }
            )
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            timeout = min(20.0, self.run["settings"]["max_seconds"] - self.elapsed())
            if timeout <= 0:
                raise BudgetExhausted("Session time budget exhausted.")
            with self.opener.open(request, timeout=timeout) as response:
                final = urllib.parse.urlsplit(response.geturl())
                if final.hostname != parsed.hostname:
                    raise AccessPaused(
                        "Platform redirected to another host; access configuration needs review."
                    )
                body = response.read(4 * 1024 * 1024 + 1)
                if len(body) > 4 * 1024 * 1024:
                    raise AccessPaused("Response exceeded the 4 MiB metadata limit.")
            if body.startswith(b"\x1f\x8b"):
                with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
                    body = compressed.read(4 * 1024 * 1024 + 1)
                if len(body) > 4 * 1024 * 1024:
                    raise AccessPaused(
                        "Decompressed response exceeded the 4 MiB metadata limit."
                    )
            self.last_receipt = {
                "request_id": request_id,
                "url": url,
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
                "transport": self.transport,
            }
            self.store.end_request(
                request_id,
                "ok",
                json.dumps(self.last_receipt)
                if self.anonymous_session
                else f"bytes:{len(body)}",
            )
            return body.decode("utf-8")
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            UnicodeError,
            EOFError,
        ) as error:
            detail = (
                f"HTTP {error.code}"
                if isinstance(error, urllib.error.HTTPError)
                else type(error).__name__
            )
            self.store.end_request(request_id, "unresolved", detail)
            if (
                isinstance(error, urllib.error.HTTPError)
                and error.code == 412
                and parsed.hostname == "api.bilibili.com"
            ):
                raise AccessPaused(
                    "Bilibili rejected the metadata request (HTTP 412). "
                    + (
                        "The signed-in session needs access review; no anonymous fallback was made."
                        if self.transport == "isolated_browser_http"
                        else "Use --use-browser with the existing isolated Bilibili login, or run login bilibili first."
                    )
                ) from None
            raise AccessPaused(
                f"Public request failed ({detail}); no conclusion about video existence was made."
            ) from None
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
            raise AccessPaused(
                "Expected JSON metadata; received an unrecognized response."
            ) from None
        if not isinstance(result, dict):
            raise AccessPaused(
                "Expected a JSON object; received an unrecognized response."
            )
        return result
