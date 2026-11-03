# Broader Vids

Broader Vids finds YouTube and Bilibili videos from your interests and feedback. It screens candidates with local metadata and assesses selected videos with captions and sampled frames. A complete round can recommend ten videos per platform.

The Node.js launcher installs and starts a separate Python engine. The engine stores your profile and database outside the plugin, then serves a local dashboard. The plugin runs on your computer. Recommendation rounds start when you request them. It does not provide cloud execution.

## Requirements

- Windows, PowerShell, Node.js 20 or later, and UV.
- Codex CLI authenticated with ChatGPT. The engine uses the model selected in the invoking chat.
- Internet access for search, downloads, and assessment.
- Bilibili requires an available browser for sign-in. Transcription and `ffprobe` are optional.

## Install from the Codex marketplace

Register the Broader Vids marketplace and install its plugin:

```powershell
codex plugin marketplace add antidemosthenes/broader-vids --sparse .agents/plugins
codex plugin add broader-vids@broader-vids
```

Start a new Codex CLI session and select the **Set up Broader Vids** skill. The ChatGPT desktop app can install Broader Vids from the configured marketplace after you register it.

For local development, see [Build and install locally](docs/REFERENCE.md#build-and-install-locally).

## Optional command-line installation

You can install the command-line package with npm:

```powershell
npm install -g broader-vids
```

This installs the CLI only. It does not register the Codex plugin or its skills.

## Set up and use

First-time setup lets you choose the included starter preset, enter interests manually, or import preferences. The starter preset contains sanitized interests, model parameters, and uncertainty from the owner's export. It contains no watched IDs, video history, topic coverage, or creator exclusions.

For an existing profile, choose whether to keep or change its preferences.
Keep preserves the current preferences and sources. It skips source selection.
Change lets you choose the starter preset, manual interests, or an import.
Then choose which sources to use.
To start fresh from an existing profile, use Reset and complete its preview and confirmation.
Then restart setup.

The setup skill guides you through platform access and local requirements. For Bilibili, sign in yourself in the separate browser window and report when you finish.

| Action | What it does | Example request or skill |
| --- | --- | --- |
| Dashboard | Rate videos, manage exclusions, and edit or transfer preferences. | Use `$broader-vids-dashboard` to open my dashboard. |
| Recommendations | Request a new manual round for your saved platforms. | Use `$broader-vids` to recommend videos. |
| Reset | Preview a fresh start while keeping installed tools and Bilibili sign-in. | Use `$broader-vids-reset` to reset my preferences and histories. |
| Clean | Preview removal of the complete local data directory. The plugin stays installed. | Use `$broader-vids-clean` to remove all of its local data. |

For setup commands, storage locations, recovery, reset, clean, and troubleshooting, see the [terminal reference](docs/REFERENCE.md). The [workflow](docs/WORKFLOW.md) explains recommendation rounds. The [architecture](docs/ARCHITECTURE.md) describes components and data flow. The [scoring guide](docs/SCORING.md) explains ranking and calibration.

Broader Vids uses the [MIT license](LICENSE).
