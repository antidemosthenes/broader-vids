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
It introduces the dashboard and asks whether you want YouTube, Bilibili, or both.
This choice becomes your default for later rounds.
It guides you through preference import or interest entry, followed by Bilibili sign-in when needed.
Required failures block setup. The report gives repair steps.
Optional tools do not block setup.
Sign in yourself in the separate browser window.
Keep it open, return to chat, and report that sign-in is complete.
Broader Vids closes the window when sign-in checks finish.
Setup ends with a final self-check and the exact selected data directory.
It shows chat requests and available skill invocations for these next actions:

- Open the local dashboard to edit broader interests, rate videos, and manage exclusions.
- Select **Preferences** in the dashboard to import preferences and learned weights.
- Request recommendations for your saved platforms.
- Preview **Reset** to start fresh while keeping tools and Bilibili sign-in.
- Preview **Clean** to remove the complete data directory while keeping the plugin installed.

An explicit setup request does not start recommendations or enable a schedule.
You can also request recommendations directly. Incomplete setup runs first.
Later rounds check readiness without repeating the introduction or reinstalling tools.
Missing requirements block the round and leave your saved data intact.

If you have an exported preference file, give its full path to the setup skill and request a restore.
You can also import it through the dashboard after setup.
Manual interest entry is unnecessary when the import supplies your interests.

Before a new round, Broader Vids reports prior recommendations with incomplete feedback.
A video remains incomplete when any of its four rating fields is blank.
Choose **Open dashboard and stop** to rate videos, then request recommendations again.
Choose **Continue this round** to proceed with blank fields unchanged.
Dismissed videos and calibration-only examples do not count.

## Import saved preferences and weights

1. Ask the **Broader Vids dashboard** skill to open the dashboard.
2. Select **Preferences**.
3. Select **Choose file** and open your exported JSON file.
4. Check the automatic preview.
5. Select **Import preferences**.
6. Edit the imported interests on the same page if needed, then select **Save**.

Import replaces learned preferences and weights. It creates a recovery file first.
It does not import calibration examples, individual feedback, recommendation history, or exclusion rules.
For a fresh profile, the recommendation and calibration lists remain empty until you request videos.
New feedback continues learning from the imported parameters.

## Request and review videos

Use the **Broader Vids recommendations** skill, or ask:

> Use Broader Vids to recommend ten YouTube videos and ten Bilibili videos.

Recommendations appear in chat.
Open the dashboard to mark videos watched, give ratings, or manage exclusions.
Select **Preferences** to change the broader subjects shared by YouTube and Bilibili.
Enter one subject per line and select **Save**. Start a new recommendation round after saving.
Select YouTube or Bilibili above the list.
Select **Rate** to open a video's separate review panel.
Calibration is optional.
Select **Token usage** to see each recommendation stage's share of reported tokens.
The report is available for completed, paused, and failed rounds.
To change Bilibili accounts, open **Exclusions → Bilibili** and select **Switch account**.
Sign in in the new window, then select **I've signed in** in the dashboard.

The default combined run allows thirty minutes.
Access failures or missing evidence can leave a run incomplete.
Use its printed batch ID to continue that run.
Unwatched videos compete with new candidates in later inspection lists.
A new round can recommend an unwatched video again.

## Skill commands, Reset, and Clean

Enter `$` in Codex and select the installed skill.
The title in the picker can contain spaces. The skill identifier does not.
Your host can show a plugin-qualified name.

| Action | Skill identifier | Example request |
| --- | --- | --- |
| Recommendations | `$broader-vids` | Use Broader Vids to recommend videos for my saved platforms. |
| Dashboard | `$broader-vids-dashboard` | Use Broader Vids to open my dashboard. |
| Reset | `$broader-vids-reset` | Use Broader Vids to reset my preferences and histories for a fresh start. |
| Clean | `$broader-vids-clean` | Use Broader Vids to remove all of its local data. |

Reset removes learned preferences, weights, ratings, exclusions, histories, results, evidence, and recovery files.
It keeps installed tools, downloaded transcription models, the selected directory, and Bilibili sign-in.
Use setup again to choose interests or import preferences.
Bilibili can import its remote blacklist again. Reset does not change that account's blacklist.

Clean removes the complete managed directory, including its runtime and local sign-in session.
The plugin stays installed. Uninstalling it first is unnecessary.
Use setup before its next use.
Exports outside the selected directory and shared package-manager caches remain intact.

Both actions first show the exact target and an estimated size.
Finish active runs and browser sign-in before applying either action.
Stop the dashboard first.
Unknown files block Clean. Reset preserves them.
See the [terminal procedure](docs/REFERENCE.md#reset-or-remove-profile-data) for explicit apply commands.

## Where Broader Vids stores data

The default Windows directory is `%LOCALAPPDATA%\broader-vids` in the environment that runs the plugin.
A host sandbox can redirect `LOCALAPPDATA` to its own directory.
Use the actual absolute path in the setup report rather than assuming your ordinary terminal uses the same default.
Broader Vids keeps its profile, imported parameters, feedback, results, browser session, and runtime outside the plugin installation.
Import reads your JSON file and stores its parameters in `broader-vids.sqlite3` under that directory.
The original JSON file stays where you saved it.
Recovery files are under `backups/` in the same data directory.

Uninstalling the plugin preserves its external data directory.
Reinstalling and selecting that same directory restores access to the saved profile.
Broader Vids supports sandbox profiles. Use the reported absolute directory across environments.
Moving or deleting an existing profile requires a separate action.

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
