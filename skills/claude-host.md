# Claude Code host guide

This guide is for Claude Code. Use it instead of the Codex-specific instructions in the five Broader Vids skill files.

## Host and engine rules

- Keep the user's selected profile stable. Run `data show` and pass its absolute path with `--data-dir` on every engine command.
- Resolve the plugin root from the installed skill path. Run the Node launcher from that root.
- Put `--host claude` before each engine subcommand. For example, run `node <plugin-root>/bin/broader-vids.cjs --host claude recommend`.
- The Node launcher handles `data show`, `data use`, `data reset`, `runtime cleanup`, `reset`, and `clean`. It consumes `--host` for these commands.
- Run `setup --host claude --evidence` only to install or repair the runtime. The Node setup command does not complete onboarding.
- Use Claude's active model and effort only when Claude exposes both values in the current session. Otherwise ask the user once for both values and wait. Never infer either value from Codex, an old setting, or a default.
- Pass the verified values with `--model MODEL --effort EFFORT` for setup completion and recommendation commands. The engine can also read `BROADER_VIDS_CLAUDE_MODEL` and `BROADER_VIDS_CLAUDE_EFFORT` when the user explicitly sets both values.
- Keep launcher commands internal. Report results and repair steps in ordinary chat.

## Setup

Use the setup skill's workflow as described here, with the engine's returned questions. Do not apply its Codex-specific host checks. Ask one question at a time. Do not invent interests, select a platform, or import a file without the user's answer.

Check Node.js, UV, Claude CLI, FFmpeg, yt-dlp, Pillow, and the selected platform access. Use the Node launcher setup command to install the runtime. Use `doctor` for diagnostics. Preserve a working profile during repair.

Successful setup removes obsolete managed runtimes and wheel copies. Report any `storage_cleanup.issues` without treating cleanup alone as an installation failure.

Use `onboarding questions`, `onboarding status`, `profile show`, and `onboarding configure` as required by the engine. Preview starter preferences or imports before applying them. Open Bilibili sign-in only when needed. Wait for the user to report sign-in completion, then check access.

Complete onboarding with `onboarding complete --format chat --model MODEL --effort EFFORT`. Include the exact active Claude model and effort. If the engine reports a required failure, repair it and repeat completion. Do not report success while an answer or required check is pending.

For a successful explicit setup request, return the full guide produced by `onboarding complete --format chat`. When setup interrupts a recommendation request, continue that request after setup succeeds.

## Recommendations

Use the recommendation skill's engine workflow, evidence rules, saved profile, and feedback gate. Run one recommendation command with `--host claude`, the verified `--model` and `--effort`, and the requested platform options.

Do not bypass incomplete feedback without the user's explicit choice. Do not reduce requested platform coverage. Report incomplete results as partial and keep candidate failure details accurate. If either platform pauses, report the original batch cause and end the request. Continue a paused run only when the user asks.

## Dashboard

Open the dashboard by running the engine's `dashboard` command with `--host claude`. Use the returned local URL. Do not start another server when one is already active. Stop only the verified Broader Vids dashboard for the selected profile.

The dashboard manages ratings, exclusions, interests, imports, and token reports. Do not create ratings or change preferences without the user's instruction. Dashboard access does not require a recommendation round.

## Reset

Run `reset` without apply flags first. Show the exact profile path, removal summary, retained items, unknown files, and blockers. Apply Reset only when the user explicitly requests it for that previewed path. Stop the verified dashboard first. Pass the same `--data-dir` and exact confirmation path. Preserve unknown files and report partial failures.

## Clean

Run `clean` without apply flags first. Show the exact profile path, removal summary, unknown files, and blockers. Apply Clean only when the user explicitly requests it for that previewed path. Stop the verified dashboard first. Pass the same `--data-dir` and exact confirmation path. Do not remove unknown files as a workaround. Report partial failures and offer setup if the user wants to use Broader Vids again.
