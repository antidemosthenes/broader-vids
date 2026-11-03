---
name: broader-vids-setup
description: Set up an installed Broader Vids plugin, its external Python runtime, local video interests, and platform access. Use for Broader Vids onboarding or installation repair, not general environment setup.
---

# Set up Broader Vids

## Completion output

After the setup steps below, make `onboarding complete --format chat` the final setup tool call.
Use the invoking chat's model and effort as described below.
When the host exposes names such as `$broader-vids:broader-vids-dashboard`, include `--skill-namespace broader-vids`.
Otherwise omit the namespace. Use the names exposed by the current host, not the plugin's display name.

For a successful explicit setup request, copy the complete returned guide into the final chat response.
Keep its platform, self-check result, data directory, warnings, and all four action descriptions, requests, and skill commands.
The guide must appear in the reply, not only in tool output.
Do not replace it with a short readiness summary or an offer to open the dashboard or recommend videos.
This applies after sign-in and even when setup already introduced these features.
The successful call exits with code zero.
It includes the text `Self-check: passed.`
If it returns a blocked JSON report or exits unsuccessfully, explain the failed checks without claiming readiness.

When a recommendation request requires setup, include the guide.
Then continue the authorized request.
Do not execute Reset or Clean while presenting their descriptions.

Keep launcher commands internal.
In user-facing replies, use chat requests, host-supported skill invocations, and relevant results or dashboard links.
Do not include raw launcher or PowerShell command lines.


Resolve the plugin root two directories above this skill to an absolute path.
Use `node <absolute-plugin-root>/bin/broader-vids.cjs` for each command below.
Run `data show` to resolve the saved profile selection.
Pass that absolute directory with `--data-dir` on subsequent commands.
An explicit directory overrides `BROADER_VIDS_DATA_DIR`, the saved selection, and the system default.
The Windows default is `%LOCALAPPDATA%/broader-vids`.
Keep user data outside the plugin cache.
Use `data use ABSOLUTE_PATH` when the user chooses another stable directory.
This command records the location without moving its data.
Use only explicit directory overrides for temporary installation tests.
Read [the reference](../../docs/REFERENCE.md) for dependency or directory details.

## Ask through the host's question tool

When the runtime is available, run `onboarding questions` to get the canonical question titles and choices.
If the runtime is missing, install it first without selecting platforms or inventing interests.
Use the host's available structured user-question tool for these questions.
Prefer `request_user_input_async` when available.
Use `request_user_input` only when the current host and mode permit it.
Do not change modes just to get a question box.
If no suitable tool exists, ask the same question in chat and wait for a reply.

Ask one question at a time. Pass the returned title and option labels to the tool.
For text questions, provide a free-text input without invented choices.
Map a selected label to its returned value.
Do not treat a preselected option, silence, or elapsed time as an answer.
With an asynchronous question tool, wait for the user's submitted reply before dependent work.
Do not report setup complete while a required answer is pending.
Do not substitute a paragraph containing all the questions for separate prompts.
A clear answer already supplied for this setup can satisfy its question without asking again.
A path mentioned in an unrelated earlier conversation does not authorize a new import.

Use the questions in this order:

1. `platform`: YouTube, Bilibili, or Both.
2. `existing_preferences` when present: Keep saved preferences or Change preferences. Keep skips preference-source and text questions.
3. `preference_source` for a new profile or an explicit change: Use calibrated starter preferences, Start fresh and enter interests, or Import my preference file.
4. `interests` for a fresh start, or `import_path` for an import. Ask for the full file path in a text box. The starter preset requires no file path.
5. Ask `bilibili_signin` after opening a sign-in window when the user selects Bilibili.

Keep paths and interests as literal data when calling the launcher.
Never execute user input as shell code.
The question UI belongs to the host. Do not claim that every host exposes the same dialog.

## Introduce and configure

Run `onboarding status` when the runtime is available.
For an incomplete profile, explain these features once during guided setup:

- Recommendations appear directly in chat.
- The dashboard provides ratings, exclusions, and optional calibration.
- Ratings improve later recommendations.
- Format 4 preferences transfer interests, learned weights, watched video IDs, compact topic coverage, and manual creator exclusions. Other video history, tag rules, and account-specific blacklists stay local.

Use the dashboard skill name that the host provides.
For first-time setup or explicit configuration, ask `platform` unless the user already chose for this setup.
An installation repair can retain an already completed profile without repeating configuration questions.
Preserve a saved platform choice during interrupted setup unless the user changes it.
Run `onboarding configure --platform youtube`, `bilibili`, or `both` after the runtime is available.
Do not repeat the introduction for a completed profile during routine checks.

## Install and check tools

Check Node.js 20 or later, UV, and Codex CLI availability.
Report exact repair steps for missing system tools.
Run `setup --evidence` to install the external runtime and evidence dependencies when needed.
Install optional transcription with `setup --asr` only when requested.
Do not reinstall a working runtime during normal recommendation requests.
If the launcher reports a missing or mismatched build receipt, refresh the runtime with `setup --evidence`.
Stop the dashboard first. The refresh preserves preferences, ratings, and sign-in.
Do not bypass this check or remove the receipt.
Before runtime repair, finish active recommendation and browser operations.
Stop this profile's verified dashboard with `dashboard stop` before changing the runtime.
A busy installation does not authorize removal of a live or uncertain lock.
If a launcher exited, check surviving children before repairing its lock.
Installation alone does not establish readiness.

Use `doctor` for local diagnostics.
Missing ffprobe does not block duration or frame checks.
Missing transcription does not block native captions or the existing visual fallback.
Not checked means unverified, not missing.
Request the host's normal permissions when the selected directory, network, or current session needs them.
If permission is unavailable, report the incomplete step and preserve completed setup.
Do not inspect credentials or change unrelated host settings to bypass a permission failure.
Assessments require ChatGPT sign-in through Codex CLI.
Use `codex login` when necessary.
Keep the existing provider and credentials intact.
Do not use API billing as a fallback.

## Restore or set interests

Run `profile show` before changing interests. Preserve existing interests and learned weights.
Ask `existing_preferences` when it is present. Keep preserves the current profile and skips preference questions.
For a new profile or an explicit change, ask `preference_source` unless the user already supplied that choice for this setup.
Never infer the choice from a saved or recently exported file.

- **Use calibrated starter preferences:** explain that this is an example user's calibrated profile, not an estimate of the new user's tastes.
  It contains interests, learned parameters, and uncertainty, with no watched IDs, video history, exploration progress, or creator exclusions.
  Preview with `profile preset starter`. Apply the requested selection with `profile preset starter --apply`.
  Do not ask for a path or invent ratings. The dashboard lets the user edit the imported interests.
- **Start fresh and enter interests:** ask `interests` in a text box. Use `profile set` with one `--interest TOPIC` per supplied interest.
  A new profile uses default priors without learned feedback.
  For an existing profile, use the Reset skill's preview and confirmation before a fresh start, then restart setup.
  Editing interests alone does not reset learned parameters.
- **Import preferences:** ask `import_path` in a text box before reading a file.
  Preview with `profile import FILE`, then apply the requested restore with `profile import FILE --apply`.
  Import accepts format 4 with learning model version 8 and feature version 1. It replaces learned parameters and both manual creator lists, and merges watched video IDs. It creates a recovery file.
  It does not import ratings, recommendation history, or other video history.
  Do not ask for manual interests after a successful import.

If a file is missing or invalid, explain the error and ask `import_path` again.
If host permissions block access, keep the supplied path and ask the host to grant access.
Do not select another file or switch to manual interests without the user's answer.

Calibration remains optional.
Do not invent feedback or copy another profile's history.

## Check access and complete setup

Before opening Bilibili sign-in, tell the user to sign in, keep the window open, and report completion in chat.
Explain that Broader Vids closes the window when sign-in checks finish.
For Bilibili, use `login bilibili` for manual sign-in in the separate browser.
After opening the window, ask the `bilibili_signin` question through the host's question tool.
For **Signed in**, use `login bilibili --confirm` and wait for the original sign-in operation to check access.
For **Still signing in**, keep the browser open and wait. Ask the status question again when the user is ready.
For **Having trouble**, ask `bilibili_trouble` in a text box. Preserve completed setup and help with the reported problem.
Do not run `login bilibili --confirm` or mark setup complete while the user reports trouble.
Do not open another sign-in window unless the user requests another attempt.
Skip this status question for YouTube-only setup or when an existing Bilibili session already passes verification.
If the user closes the window before verification, explain the incomplete verification.
Wait for the user to request another attempt before opening a new sign-in window.
Wait for verification from the original sign-in operation.
The confirmation request alone does not prove successful sign-in.
Do not request passwords or cookie text.
A failed blacklist import preserves saved rules and does not undo successful sign-in.

Run `onboarding complete --format chat` after configuration, interests, tools, and access are ready.
This command checks the selected platforms before recording completion.
If the user requested transcription, include `--asr-model base` or their chosen supported model.
This verifies the requested transcription dependency without downloading a model or analyzing audio.
It uses the invoking chat's model and effort.
Supply both `--model` and `--effort` from the host when inheritance is unavailable.
Report missing, unusable, unverified, and busy checks separately.
Required failures block completion.
Optional warnings do not block completion.
Repair the reported requirement and repeat completion without resetting preferences.
If the runtime rejects `--format` or `--skill-namespace`, it predates this plugin.
Stop the verified dashboard and run `setup --evidence` once to install the bundled runtime, then repeat completion.

## Finish setup

Return the canonical guide as specified in Completion output.
Do not repeat the checks just to generate another report.
Resolve incomplete setup instead of using a separate success template.
The selected data directory stays separate from the plugin bundle.
Broader Vids preserves this directory after you uninstall the plugin.
If the path is inside a host sandbox, explain that another environment can resolve a different default.
Do not silently move an existing profile or substitute an empty profile at another path.

An explicit setup request ends after this guidance.
If setup interrupted a recommendation request, return to the recommendation skill and continue that request.
