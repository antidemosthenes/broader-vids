import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from broader_vids.cli.environment import environment_report
from broader_vids.evidence.browser import bilibili_browser
from broader_vids.processes import run_bounded


def result(status, remedy, **values):
    return {"status": status, "remedy": remedy, **values}


def chatgpt_check():
    executable = shutil.which("codex")
    remedy = (
        "Run codex login and sign in with ChatGPT. Do not use an API key as a fallback."
    )
    if not executable:
        return result("missing", "Install Codex CLI, then sign in with ChatGPT.")
    environment = os.environ.copy()
    for name in (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    ):
        environment.pop(name, None)
    try:
        report = run_bounded(
            [executable, "login", "status"], env=environment, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return result("unverified", remedy)
    if (
        report.returncode == 0
        and "Logged in using ChatGPT" in report.stdout + report.stderr
    ):
        return result("available", remedy)
    return result("missing", remedy)


def bilibili_check(root, token):
    remedy = "Run login bilibili, finish sign-in in the browser, then run login bilibili --confirm."
    directory = root / "access/bilibili"
    if (directory / "switch-commit.json").exists():
        return result(
            "busy",
            "Open the dashboard, select Exclusions and Bilibili, then select Finish recovery before checking access again.",
        )
    if (directory / "browser.lock").exists():
        return result(
            "busy",
            "Close the active Bilibili operation, then retry. Do not remove a live browser lock.",
        )
    if not (directory / "profile").is_dir():
        return result("missing", remedy)
    try:
        with bilibili_browser(root, lock_token=token) as context:
            try:
                response = context.request.get(
                    "https://api.bilibili.com/x/web-interface/nav", timeout=5000
                )
                value = response.json() if response.ok else None
            except Exception:
                return result(
                    "unverified",
                    "Bilibili access could not be checked. Check network access and retry.",
                )
            if isinstance(value, dict) and value.get("code") == -101:
                return result("missing", remedy)
            if (
                not isinstance(value, dict)
                or type(value.get("code")) is not int
                or not isinstance(value.get("data"), dict)
            ):
                return result(
                    "unverified",
                    "Bilibili returned an unsupported response. Retry access verification.",
                )
            data = value["data"]
            if value["code"] == -101 or data.get("isLogin") is False:
                return result("missing", remedy)
            if (
                value["code"] != 0
                or data.get("isLogin") is not True
                or type(data.get("mid")) is not int
                or data["mid"] <= 0
            ):
                return result(
                    "unverified",
                    "Bilibili access could not be verified. Retry or complete manual sign-in.",
                )
            return result("available", remedy, account_id=str(data["mid"]))
    except ValueError:
        return result(
            "busy" if (directory / "browser.lock").exists() else "unusable",
            "Close any active sign-in window. Install the evidence extra and Edge, Chrome, or Playwright Chromium, then retry.",
        )
    except Exception:
        return result(
            "unusable",
            "The browser could not start or close correctly. Install Edge, Chrome, or Playwright Chromium and retry.",
        )


def inspect(root, platform, token, asr):
    environment = environment_report(root)
    checks = {
        name: {
            **item,
            "status": "unusable" if item["status"] == "unsupported" else item["status"],
        }
        for name, item in environment["required"].items()
    }
    for name in ("yt_dlp", "PIL", "opencc"):
        if name in checks and checks[name]["status"] == "available":
            try:
                importlib.import_module(name)
                if name == "opencc":
                    from broader_vids.discovery.languages import search_query

                    search_query("bilibili", "繁體中文")
            except Exception:
                checks[name] = {**checks[name], "status": "unusable"}
    checks["chatgpt_sign_in"] = chatgpt_check()
    if platform in {"bilibili", "both"}:
        checks["playwright"] = environment["conditional"]["playwright"]
        if checks["playwright"]["status"] == "available":
            checks["bilibili_access"] = bilibili_check(root, token)
    optional = dict(environment["optional"])
    if asr:
        checks["faster_whisper"] = optional.pop("faster_whisper")
        if checks["faster_whisper"]["status"] == "available":
            try:
                importlib.import_module("faster_whisper")
            except Exception:
                checks["faster_whisper"]["status"] = "unusable"
    return {
        "checks": checks,
        "warnings": {
            name: item
            for name, item in optional.items()
            if item["status"] != "available"
        },
    }


if __name__ == "__main__":
    print(
        json.dumps(
            inspect(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4] == "asr")
        )
    )
