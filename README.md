# Broader Vids

Broader Vids finds YouTube and Bilibili videos from your interests and feedback. It screens candidates with local metadata and assesses selected videos with captions and sampled frames. A complete round can recommend ten videos per platform.

## How it works

The plugin skills guide requests and setup. A Node.js launcher installs and checks a separate Python engine in your data directory. The engine stores your profile and SQLite database there, then serves an optional local dashboard for ratings, preferences, and exclusions.

Recommendation rounds start when you request them.

## Requirements

- Windows, PowerShell, Node.js 20 or later, and UV.
- Codex CLI with ChatGPT sign-in. The engine uses the model selected in the invoking chat.
- Internet access for search, downloads, and assessment.
- Bilibili requires an available browser for sign-in. Transcription and `ffprobe` are optional.

## Prepare the local plugin

Run these commands from the repository folder:

```powershell
uv sync --locked
uv build --wheel --out-dir python
npm pack --ignore-scripts
uv run --no-sync scripts/build_plugin.py
```

This prepares `dist/plugins/broader-vids` for local installation. It does not publish the plugin.

### Install with Codex CLI

```powershell
codex login
codex plugin marketplace add .
codex plugin add broader-vids@broader-vids-local
```

Start a new Codex CLI session and select the **Set up Broader Vids** skill.

### Install in the ChatGPT desktop app

Open this repository as a Codex project, restart the app, then install **Broader Vids** from the **Broader Vids (local)** marketplace. Start a new conversation and select **Set up Broader Vids**.

## Set up and use

Setup asks which platform to use and whether to enter interests, import preferences, or keep saved preferences. It checks local requirements and verifies platform access. For Bilibili, sign in yourself in the separate browser window and report when you finish.

The setup skill returns a guide with these four actions:

| Action | What it does | Example request or skill |
| --- | --- | --- |
| Dashboard | Rate videos, manage exclusions, and open Preferences to edit or transfer interests and learned weights. | Use `$broader-vids-dashboard` to open my dashboard. |
| Recommendations | Request a new manual round for your saved platforms. | Use `$broader-vids` to recommend videos. |
| Reset | Preview a fresh start while keeping installed tools and Bilibili sign-in. | Use `$broader-vids-reset` to reset my preferences and histories. |
| Clean | Preview removal of the complete local data directory. The plugin stays installed. | Use `$broader-vids-clean` to remove all of its local data. |

Use **Reset** to remove learning, ratings, histories, and results while keeping installed tools and Bilibili sign-in. Use **Clean** to remove the complete profile directory. Both actions show a preview first.

For import steps, storage locations, recovery, reset, clean, and troubleshooting, see the [terminal and maintenance reference](docs/REFERENCE.md). The [workflow](docs/WORKFLOW.md) explains recommendation rounds. The [architecture](docs/ARCHITECTURE.md) describes components and data flow. The [scoring guide](docs/SCORING.md) explains ranking and calibration.

Broader Vids uses the [MIT license](LICENSE).
