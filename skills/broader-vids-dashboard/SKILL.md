---
name: broader-vids-dashboard
description: Open, inspect, or stop the Broader Vids local dashboard for recommendation feedback, optional calibration, token usage reports, and creator or tag exclusions. Use only for Broader Vids dashboard requests.
---

# Broader Vids dashboard

Keep launcher commands internal.
In user-facing replies, use chat requests, host-supported skill invocations, and relevant results or dashboard links.
Do not include raw launcher or PowerShell command lines.


Resolve the plugin root two directories above this skill to an absolute path.
Use `node <absolute-plugin-root>/bin/broader-vids.cjs` with the user's stable external data directory.
Resolve the same directory used by recommendations before opening the dashboard.
Run `data show` to find the saved selection when the conversation has no explicit directory.
If the runtime is missing, use the packaged [setup skill](../broader-vids-setup/SKILL.md).

For an open request, run `dashboard open` with an explicit absolute `--data-dir`.
The command starts or reuses that profile's server and requests the system browser.
Present the returned URL as a clickable link.
`browser_opened: true` means the system accepted the browser request. It does not prove that the page rendered.
If `browser_opened` is false, show the returned fallback message and link.
Use `--no-browser` when the user requests only a link or server startup.
Use the host's embedded browser only when the user requests it.
If that browser stays blank or reports an attachment error, use the system browser instead.
Check `dashboard status` before restarting a server that appears inaccessible.
Do not reset setup or profile data to repair a browser-opening failure.
Do not assume port 8765 belongs to this profile.
The command chooses another port if another service uses the preferred port.

Use `dashboard status` to inspect the verified server.
Use `dashboard stop` when the user asks to stop it.
This stops only the server registered for the selected profile.
Closing a browser tab does not stop the server.
Never stop another process based only on its port or an old PID.

Recommendations, exclusions, and optional calibration have separate views.
Use Preferences to edit broader interests and import or export learned weights.
Imported interests appear in the same editor. Save any later edits before exporting.
Interest edits keep ratings and calibration. Finish active recommendation work before saving.
Start a new round after changing interests. Paused rounds with an older profile cannot continue.
Preferences shows current interests before any import. The user can edit and save them directly.
The built-in 视频翻译 exclusion is removable under Exclusions → Bilibili → Tags.
Add the same tag again to restore it. Imported Bilibili creator bans remain managed on Bilibili.
Feedback and creator rules save through their existing controls.
To change Bilibili accounts, use Switch account under Exclusions → Bilibili.
The user signs in manually in a separate window.
Tell the user to keep that window open, return to the dashboard, and select I've signed in.
Broader Vids closes the window when sign-in checks finish.
Cancel switch retains the previous session until activation starts.
Finish active Bilibili work before switching.
Preferences and ratings remain unchanged.
Do not request cookies or credentials in chat.
Do not create ratings merely by opening the page.
Do not start a new recommendation round to show existing results.
If a recommendation request stops for feedback, open this dashboard and end that request.
Do not resume when the user saves ratings.
The user must invoke recommendations again.
Format 4 preferences transfer interests, learned weights, watched video IDs, compact topic coverage, and manual creator exclusions.
Import replaces both local manual creator lists with the lists in the file. An empty list clears that platform's manual rules.
Bilibili blacklist sync preserves manual rules and combines them with the active account's imported blacklist.
It does not transfer full recommendation or calibration history.
Imports accept format 4 with learning model version 8 and feature version 1.
Token usage reports saved recommendation rounds, including paused or failed rounds.
The user can select a round to see stage percentages, input, output, cached input, and reporting coverage.
Unreported usage stays unknown. The report does not start model calls.
Opening the dashboard does not require recommendation preflight or completed onboarding.
Keep recommendations available directly in chat through the recommendation skill.
