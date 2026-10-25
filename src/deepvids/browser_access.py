from contextlib import contextmanager
import http.cookiejar
import json
from pathlib import Path
import time

from .storage import utc_now


def browser_channel():
    if Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe").is_file():
        return "msedge"
    if Path("C:/Program Files/Google/Chrome/Application/chrome.exe").is_file():
        return "chrome"
    return None


@contextmanager
def bilibili_browser(root, visible=False):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ValueError("Install the evidence extra with UV before using browser sign-in.") from None
    profile = Path(root) / "access" / "bilibili" / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    lock = profile.parent / "browser.lock"
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError:
        raise ValueError("The isolated Bilibili browser is already in use; close its active run first.") from None
    try:
        with handle, sync_playwright() as playwright:
            options = {"headless": not visible, "accept_downloads": False, "viewport": {"width": 1200, "height": 850},
                       "args": ["--new-window", "--no-first-run", "--no-default-browser-check"]}
            channel = browser_channel()
            if channel:
                options["channel"] = channel
            context = playwright.chromium.launch_persistent_context(str(profile), **options)
            try:
                yield context
            finally:
                context.close()
    finally:
        lock.unlink(missing_ok=True)


def confirm_bilibili_login(root):
    directory = Path(root) / "access" / "bilibili"
    if not (directory / "browser.lock").is_file():
        raise ValueError("No isolated sign-in browser is running. Start login first.")
    (directory / "confirm.request").write_text(utc_now(), encoding="utf-8")
    return {"confirmation_requested": True, "authenticated": "pending verification in the open browser"}


def login_bilibili(root, timeout=600):
    if not 30 <= timeout <= 1800:
        raise ValueError("Browser sign-in requires a timeout between 30 and 1,800 seconds.")
    with bilibili_browser(root, visible=True) as context:
        page = context.pages[0] if context.pages else context.new_page()
        confirmation = Path(root) / "access" / "bilibili" / "confirm.request"
        confirmation.unlink(missing_ok=True)
        page.goto("https://www.bilibili.com/", wait_until="domcontentloaded", timeout=30000)
        print("Bilibili is open in its isolated window. Use the site's sign-in button, then run login bilibili --confirm. No credentials are printed.", flush=True)
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            if confirmation.exists():
                confirmation.unlink()
                cookies = context.cookies(["https://www.bilibili.com/", "https://api.bilibili.com/"])
                names = {c["name"] for c in cookies if c["value"]}
                if not {"SESSDATA", "DedeUserID"}.issubset(names):
                    print("An authenticated session is not established yet; the browser remains open.", flush=True)
                    continue
                try:
                    response = context.request.get("https://api.bilibili.com/x/web-interface/nav", timeout=15000)
                    verified = response.ok and response.json().get("data", {}).get("isLogin") is True
                except Exception:
                    verified = False
                if verified:
                    status = {"authenticated": True, "checked_at": utc_now(), "storage": "isolated_browser_profile"}
                    target = Path(root) / "access" / "bilibili" / "status.json"
                    target.write_text(json.dumps(status, indent=2), encoding="utf-8")
                    return status
                print("The site did not verify the session; access remains pending and the browser stays open.", flush=True)
            if not context.pages:
                raise ValueError("The sign-in browser was closed before authentication completed.")
            page = next((p for p in context.pages if not p.is_closed()), page)
            try:
                page.wait_for_timeout(1000)
            except Exception:
                raise ValueError("The sign-in browser closed before authentication was verified; access remains pending.") from None
    raise ValueError("Bilibili sign-in timed out; no authenticated access was established.")


def browser_cookie_jar(context):
    jar = http.cookiejar.CookieJar()
    for item in context.cookies(["https://www.bilibili.com/", "https://api.bilibili.com/"]):
        domain = item["domain"]
        if domain.lstrip(".") != "bilibili.com" and not domain.endswith(".bilibili.com"):
            continue
        jar.set_cookie(http.cookiejar.Cookie(0, item["name"], item["value"], None, False, domain, True,
                                             domain.startswith("."), item["path"], True, item.get("secure", True),
                                             int(item["expires"]) if item.get("expires", -1) > 0 else None,
                                             item.get("expires", -1) <= 0, None, None, {}, False))
    return jar
