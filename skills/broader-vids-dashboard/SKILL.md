---
name: broader-vids-dashboard
description: Open, inspect, or stop the Broader Vids local dashboard for recommendation feedback, optional calibration, and creator or tag exclusions. Use only for Broader Vids dashboard requests.
---

# Broader Vids dashboard

Resolve the plugin root two directories above this skill to an absolute path.
Use `node <absolute-plugin-root>/bin/broader-vids.cjs` with the user's stable external data directory.
Resolve the same directory used by recommendations before opening the dashboard.
Run `data show` to find the saved selection when the conversation has no explicit directory.
If the runtime is missing, use the packaged [setup skill](../broader-vids-setup/SKILL.md).

Run `dashboard open --no-browser` with an explicit absolute `--data-dir`.
The command starts or reuses that profile's server and returns its local URL.
Present the URL as a clickable link.
Use the host's browser-opening capability if the user requested a browser window.
For an ordinary terminal, `dashboard open` also opens the system browser.
Do not assume port 8765 belongs to this profile.
The command chooses another port if another service uses the preferred port.

Use `dashboard status` to inspect the verified server.
Use `dashboard stop` when the user asks to stop it.
This stops only the server registered for the selected profile.
Closing a browser tab does not stop the server.
Never stop another process based only on its port or an old PID.

Recommendations, exclusions, and optional calibration have separate views.
Feedback and creator rules save through their existing controls.
Do not create ratings merely by opening the page.
Do not start a new recommendation round to show existing results.
Keep recommendations available directly in chat through the recommendation skill.
