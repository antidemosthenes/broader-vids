# Broader Vids

Broader Vids recommends YouTube and Bilibili videos from your interests and feedback.
It searches beyond familiar topics and keeps older videos eligible.
Each complete round produces ten recommendations per platform.
Captions and selected frames support quality assessment.
You can rate topic interest, depth, and quality separately.

This preview runs locally through Codex CLI or Codex in the ChatGPT desktop app.
Recommendation rounds are manual. Automatic daily runs remain planned.
The project is not yet published to npm or the public Plugins Directory.

## Requirements

- Windows with PowerShell.
- [Node.js](https://nodejs.org/) 20 or later, including npm.
- [UV](https://docs.astral.sh/uv/getting-started/installation/) to manage Python 3.12 or later.
- [Codex CLI](https://learn.chatgpt.com/docs/cli) with ChatGPT sign-in and access to the model you select.
- The ChatGPT desktop app for installation through the app.
- Internet access for discovery, downloads, and AI assessment.

Bilibili access also needs Edge, Chrome, or Playwright Chromium for manual sign-in.
Setup installs the media and browser libraries.
Speech transcription is optional and helps when captions are unavailable.
`ffprobe` is optional. Broader Vids does not need an OpenAI API key.

## Prepare the local preview

Both installation methods currently use this repository's local marketplace.
Download or clone the repository. Open PowerShell in its root folder.
Build the plugin files once:

```powershell
uv sync --locked
uv build --wheel --out-dir python
npm pack --ignore-scripts
uv run --no-sync scripts/build_plugin.py
```

The prepared plugin is at `dist/plugins/broader-vids`.
The repository's marketplace file points to that directory.
If these files are already prepared, continue with either method below.
These commands build local files. They do not publish or install the plugin.

## Option 1: Install with Codex CLI

Run these commands from the repository root:

```powershell
codex login
codex plugin marketplace add .
codex plugin add broader-vids@broader-vids-local
```

Start a new Codex CLI session with `codex`.
Continue with **Set up Broader Vids** below.
You can also use `/plugins` inside Codex CLI to inspect the local marketplace and install its plugin.
See the [official plugin guide](https://learn.chatgpt.com/docs/plugins).

## Option 2: Install in the ChatGPT desktop app

1. Open this repository as a local Codex project in the ChatGPT desktop app.
2. Restart the app after preparing the plugin files.
3. Open **Plugins** and select the **Broader Vids (local)** marketplace.
4. Select **Broader Vids**, then **Install**.
5. Start a new Codex conversation in the app.
6. Continue with **Set up Broader Vids** below.

The app discovers the repository catalog at `.agents/plugins/marketplace.json`.
This is a local marketplace installation, not a public directory listing.
See the [official local plugin instructions](https://developers.openai.com/plugins/build/plugins#install-a-local-plugin-manually).

## Set up Broader Vids

Select the installed **Set up Broader Vids** skill in the new conversation.
You can also ask:

> Use Broader Vids to set up this computer with the default data directory.

Setup installs the separate Python runtime and checks its tools.
It guides you through interests and Bilibili sign-in.
Sign in yourself in the separate browser window.
Setup does not start recommendations or enable a schedule.

If you have an exported preference file, give its full path to the setup skill and request a restore.
You can also import it through the dashboard after setup.
Manual interest entry is unnecessary when the import supplies your interests.

## Import saved preferences and weights

1. Ask the **Broader Vids dashboard** skill to open the dashboard.
2. Select **Import/Export**.
3. Select **Choose file** and open your exported JSON file.
4. Check the automatic preview.
5. Select **Import preferences**.

Import replaces learned preferences and weights. It creates a recovery file first.
It does not import calibration examples, individual feedback, recommendation history, or exclusion rules.
For a fresh profile, the recommendation and calibration lists remain empty until you request videos.
New feedback continues learning from the imported parameters.

## Request and review videos

Use the **Broader Vids recommendations** skill, or ask:

> Use Broader Vids to recommend ten YouTube videos and ten Bilibili videos.

Recommendations appear in chat.
Open the dashboard to mark videos watched, give ratings, or manage exclusions.
Select YouTube or Bilibili above the list.
Select **Rate** to open a video's separate review panel.
Calibration is optional.

The default combined run allows thirty minutes.
Access failures or missing evidence can leave a run incomplete.
Use its printed batch ID to continue that run.
Unwatched videos compete with new candidates in later inspection lists.
A new round can recommend an unwatched video again.

## Where Broader Vids stores data

The default Windows directory is `%LOCALAPPDATA%\broader-vids`.
Broader Vids keeps its profile, imported parameters, feedback, results, browser session, and runtime outside the plugin installation.
Import reads your JSON file and stores its parameters in `broader-vids.sqlite3` under that directory.
The original JSON file stays where you saved it.
Recovery files are under `backups/` in the same data directory.

A normal installation does not create a `.localdata` folder in this repository.
An explicit data selection or override can choose another location.
Use the same directory for setup, the dashboard, and recommendations.
See [data directory selection](docs/REFERENCE.md#choose-a-data-directory).

## More documentation

- [Workflow](docs/WORKFLOW.md): recommendation steps and feedback.
- [Architecture](docs/ARCHITECTURE.md): files, folders, and data flow.
- [Scoring](docs/SCORING.md): quality, calibration, and mathematics.
- [Reference](docs/REFERENCE.md): terminal commands, tests, builds, dependencies, and copyright.

Broader Vids uses the [MIT license](LICENSE).
