---
name: broader-vids-setup
description: Set up an installed Broader Vids plugin, its external Python runtime, local video interests, and platform access. Use for Broader Vids onboarding or installation repair, not general environment setup.
---

# Set up Broader Vids

Resolve the plugin root two directories above this skill to an absolute path.
Use `node <absolute-plugin-root>/bin/broader-vids.cjs` for each command below.
Run `data show` to resolve the saved profile selection.
An explicit `--data-dir` takes precedence over `BROADER_VIDS_DATA_DIR`, the saved selection, and the system default.
Keep that directory outside the plugin cache and use it on every command.
The default is `%LOCALAPPDATA%/broader-vids` on Windows.
Use `data use ABSOLUTE_PATH` when the user selects a stable profile for later conversations.
This records its location without moving or copying its data.
Use only explicit `--data-dir` overrides for temporary installation tests.
Read [the reference](../../docs/REFERENCE.md) when the user needs another location or dependency details.

Check Node.js 20 or later, UV, and Codex CLI availability.
Run `setup --asr` to install the engine and the media, browser, and optional transcription dependencies.
This installation does not require administrator access.
Run `doctor` and `codex login status`.
Use the report's Required, Conditional, Optional, and Manual checks groups.
Only missing or unusable required items fail the local check.
Conditional items block only the workflow in their required_for field.
Missing ffprobe does not block Broader Vids duration or frame checks.
Missing faster-whisper affects videos without usable native captions.
Do not install optional tools merely because their status is missing.
Not checked means unverified, not missing.
Check account access separately before reporting that recommendations are ready.
Request the host's normal permission for the selected external directory, network, or existing Codex session when necessary.
If permission is unavailable, report the incomplete step and preserve completed setup.
Do not repair the host environment or inspect credential files to bypass a permission failure.
Assessments require ChatGPT sign-in. Use `codex login` if needed.
Keep the existing model provider and credentials intact.
Do not use API billing as a fallback.

Run `profile show` before changing interests.
Preserve an existing profile unless the user asks to change it.
For a requested preference restore, preview the supplied JSON file with `profile import FILE`.
Then apply the requested restore with `profile import FILE --apply`.
Import replaces learned parameters and creates a recovery file without importing video history.
Skip manual interest entry when the restored file supplies interests.
For a fresh profile, ask for broad interests when the user has not supplied them.
Use `profile set --interest TOPIC` once for each supplied interest.
Fresh profiles can request recommendations immediately.
Development `gate` results and calibration quotas do not block normal use.
Do not invent feedback or copy another profile's history.

If the user wants Bilibili, use `login bilibili` for the separate manual sign-in browser.
After the user finishes, use `login bilibili --confirm`.
Explain an access challenge and retain completed setup when verification fails.
Do not request passwords or cookie text.
A failed blacklist import preserves saved rules and does not undo successful sign-in.

Report the selected data directory, installed tools, interests, and access status.
Separate required failures, conditional limitations, and optional omissions in that report.
Offer the installed recommendation and dashboard skills as the next actions.
Use the invocation names exposed by the current host.
Installation does not start a recommendation run or enable a schedule automatically.
