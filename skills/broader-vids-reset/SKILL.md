---
name: broader-vids-reset
description: Reset Broader Vids preferences, learned weights, ratings, exclusions, and histories for a fresh start while retaining tools and Bilibili sign-in.
---

# Reset Broader Vids

Keep launcher commands internal.
In user-facing replies, use chat requests, host-supported skill invocations, and relevant results or dashboard links.
Do not include raw launcher or PowerShell command lines.


Resolve the plugin root two directories above this skill to an absolute path.
Use `node <absolute-plugin-root>/bin/broader-vids.cjs` for each command.
Run `data show` to resolve the selected profile.
Pass that exact absolute directory with `--data-dir` on every later command.
The current host can redirect `LOCALAPPDATA`.
Do not substitute the ordinary terminal's default or another profile.

Run `reset` without flags to preview the action.
Show the absolute target, estimated size, removed items, retained items, unknown files, and blockers.
Reset removes preferences, learned weights, ratings, exclusions, histories, results, evidence, and recovery files.
Installed tools, transcription models, the selected directory, and Bilibili sign-in remain.
Export files outside the selected directory remain intact.
An implementation request or a question about this action does not authorize deletion of a real profile.

Proceed only when the user explicitly requests this destructive action for the shown profile.
If intent is ambiguous, show the preview and ask before applying.
An explicit request already authorizes the action. Do not ask again merely because the command requires flags.
Stop this profile's verified dashboard with `dashboard stop` before applying.
Wait for the dashboard process to finish if the operation still reports it busy.
Do not cancel active recommendations or browser sign-in to force this action.
Do not remove live or uncertain locks.
Reset preserves unknown files. Do not include those files in deletion commands.

Run `reset --apply --confirm-path ABSOLUTE_PATH` with the exact previewed directory.
Use the Node launcher so no Python process remains inside a directory that Clean must remove.
Report success only when the command returns `completed` or `absent`.
For an error or partial result, explain the failure and preserve the reported remaining files.
Report the remaining data after a partial failure.
Do not uninstall the plugin or remove shared UV or npm caches.
After Reset, offer the setup skill to choose interests or import preferences.
