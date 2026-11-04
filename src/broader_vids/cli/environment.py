import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import sys

from broader_vids import __version__
from broader_vids.evidence.browser import browser_channel
from broader_vids.evidence.media import EvidencePending, ffmpeg_executable


def executable_version(executable, argument):
    result = subprocess.run(
        [executable, argument],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=5,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise ValueError("The executable did not pass its version check.")
    return result.stdout.splitlines()[0] if result.stdout else ""


def tool(name, purpose, remedy):
    path = shutil.which(name)
    return {
        "status": "available" if path else "missing",
        "purpose": purpose,
        "path": path,
        "remedy": remedy,
    }


def dependency(name, purpose, remedy):
    return {
        "status": "available" if importlib.util.find_spec(name) else "missing",
        "purpose": purpose,
        "remedy": remedy,
    }


def environment_report(root, *, host="codex"):
    from broader_vids.worker_context import validate_host

    host = validate_host(host)
    evidence_remedy = "Use setup --evidence in the Node launcher, or uv sync --locked --extra evidence in the source folder."
    host_cli = "codex" if host == "codex" else "claude"
    host_purpose = (
        "Run AI planning and video assessment through your ChatGPT subscription."
        if host == "codex"
        else "Run AI planning and video assessment through your authenticated Claude Code CLI."
    )
    required = {
        "python": {
            "status": "available" if sys.version_info >= (3, 12) else "unsupported",
            "version": sys.version.split()[0],
            "purpose": "Run the engine with Python 3.12 or later.",
            "remedy": "Use UV to install Python 3.12 or later.",
        },
        "uv": tool("uv", "Install and update the external runtime.", "Install UV."),
        "node": tool(
            "node",
            "Run the launcher and YouTube JavaScript support with Node.js 20 or later.",
            "Install Node.js 20 or later.",
        ),
        host_cli: tool(
            host_cli,
            host_purpose,
            f"Install {host_cli.capitalize()} CLI.",
        ),
        "yt_dlp": dependency(
            "yt_dlp", "Retrieve video metadata, captions, and media.", evidence_remedy
        ),
        "opencc": dependency(
            "opencc",
            "Normalize Bilibili search keywords to Simplified Chinese.",
            "Use setup in the Node launcher, or uv sync --locked in the source folder.",
        ),
        "PIL": dependency(
            "PIL", "Prepare frames for video assessment.", evidence_remedy
        ),
    }
    if required["uv"]["path"]:
        try:
            version = executable_version(required["uv"]["path"], "--version")
            if not version.startswith("uv "):
                raise ValueError("The executable did not report a UV version.")
            required["uv"]["version"] = version
        except (OSError, ValueError, subprocess.SubprocessError):
            required["uv"]["status"] = "unusable"
    if required["node"]["path"]:
        try:
            version = executable_version(required["node"]["path"], "--version")
            match = re.fullmatch(r"v?(\d+)\.\d+\.\d+(?:[-+].*)?", version)
            required["node"].update(
                version=version,
                status="available" if match and int(match[1]) >= 20 else "unsupported",
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            required["node"]["status"] = "unusable"
    required["ffmpeg"] = {
        "status": "missing",
        "purpose": "Verify video duration and dimensions, decode media, and extract frames.",
        "remedy": evidence_remedy,
    }
    try:
        executable = ffmpeg_executable()
        required["ffmpeg"]["path"] = executable
        version = executable_version(executable, "-version")
        if not version.lower().startswith("ffmpeg version"):
            raise ValueError("The executable did not report an FFmpeg version.")
        required["ffmpeg"].update(
            version=version,
            status="available",
        )
    except (
        EvidencePending,
        OSError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
    ):
        if "path" in required["ffmpeg"]:
            required["ffmpeg"]["status"] = "unusable"
    channel = browser_channel()
    missing = [name for name, item in required.items() if item["status"] != "available"]
    manual_checks = {
        "chatgpt_sign_in": {
            "status": "not_checked",
            "required_for": "AI planning and assessment",
            "verification": "Run codex login status and verify ChatGPT sign-in.",
        },
        "internet_access": {
            "status": "not_checked",
            "required_for": "Remote video access and AI assessment",
            "verification": "Allow the host's network requests when needed.",
        },
    }
    if host == "claude":
        manual_checks = {
            "claude_sign_in": {
                "status": "not_checked",
                "required_for": "AI planning and assessment",
                "verification": "Complete Claude CLI sign-in, then retry readiness checks.",
            },
            "internet_access": manual_checks["internet_access"],
        }
    return {
        "version": __version__,
        "scope": "Local setup requirements for video recommendations",
        "data_directory": str(Path(root)),
        "local_requirements_met": not missing,
        "missing_required": missing,
        "required": required,
        "conditional": {
            "playwright": {
                **dependency(
                    "playwright",
                    "Use the isolated Bilibili sign-in and authenticated video browser.",
                    evidence_remedy,
                ),
                "required_for": "Bilibili browser access",
            },
            "browser": {
                "status": "available" if channel else "not_checked",
                "detected_channel": channel,
                "required_for": "Bilibili browser access",
                "purpose": "Use Edge, Chrome, or Playwright Chromium.",
                "note": "The check detects common system browsers. It does not launch a browser or inspect cached Chromium.",
            },
            "bilibili_sign_in": {
                "status": "not_checked",
                "required_for": "Full Bilibili video access",
                "verification": "Complete login bilibili, then login bilibili --confirm.",
            },
        },
        "optional": {
            "faster_whisper": dependency(
                "faster_whisper",
                "Transcribe videos when native captions are unavailable. Captioned videos do not need this tool.",
                "Use setup --asr in the Node launcher, or uv sync --locked --extra evidence --extra asr in the source folder.",
            ),
            "ffprobe": tool(
                "ffprobe",
                "Provide additional media metadata utilities. Broader Vids uses FFmpeg directly for its duration and frame checks.",
                "No installation is required for the current Broader Vids checks.",
            ),
        },
        "manual_checks": manual_checks,
        "notes": [
            "Missing optional tools do not fail this check.",
            "Conditional requirements affect only their named workflows.",
            "Not checked does not mean missing. Local tool checks do not verify sign-in, browser launch, or network access.",
        ],
    }
