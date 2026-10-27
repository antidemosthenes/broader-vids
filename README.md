# Deepvids

Deepvids recommends YouTube and Bilibili videos from your interests and feedback.
It aims to find useful videos beyond the topics that you already watch.
Older videos remain eligible.

Each complete run produces ten YouTube recommendations and ten Bilibili recommendations.
Deepvids checks captions and selected frames before it ranks videos.
You can rate a topic, the content depth, and the video quality separately.
Your profile, feedback, and results stay on your computer.

The current preview supports manual runs from a terminal.
Codex plugin installation and automatic daily runs are not ready yet.

## Requirements

- Windows with PowerShell.
- [UV](https://docs.astral.sh/uv/getting-started/installation/) to manage Python 3.12 or later.
- [Codex CLI](https://developers.openai.com/codex/cli/) with an active ChatGPT sign-in and access to GPT-6.1 Sol.
- Edge or Chrome for the separate Bilibili sign-in window.
- Internet access for video discovery and analysis.

Deepvids uses your Codex access for model calls.
It does not require an OpenAI API key.

## Start from the source folder

Open PowerShell in the project folder.
Install the dependencies:

```powershell
uv sync --locked --extra evidence --extra asr
uv run --no-sync deepvids doctor
codex login
```

The `asr` option installs speech transcription tools for videos without usable captions.
Model weights download when transcription first needs them.

Set your interests:

```powershell
uv run --no-sync deepvids profile set --interest mathematics --interest travel --interest music
```

Sign into Bilibili in the separate browser window:

```powershell
uv run --no-sync deepvids login bilibili
```

Complete sign-in in that window.
Keep the original terminal open.
Use a second PowerShell terminal in the project folder to check the saved session:

```powershell
uv run --no-sync deepvids login bilibili --confirm
```

Request recommendations:

```powershell
uv run --no-sync deepvids recommend both --use-browser --asr-model base
```

Open the review page:

```powershell
uv run --no-sync deepvids serve
```

Visit [the local review page](http://127.0.0.1:8765).
Use its controls to mark videos watched and give feedback.
Press **Ctrl+C** in the server terminal to stop the page server.

## What to expect

The default run allows 30 minutes for both platforms.
Access failures or missing evidence can leave a run incomplete.
Use the printed batch ID to continue that run.
See the [terminal reference](docs/REFERENCE.md#continue-an-incomplete-run).

An unwatched recommendation keeps its place in later inspection lists until you watch or dismiss it.
A new run can recommend that video again.
Watching and rating are separate actions.

Deepvids stores data under `%LOCALAPPDATA%\deepvids` by default.
Use the same data directory for every command.
The [terminal reference](docs/REFERENCE.md#choose-a-data-directory) explains how to select another directory.

## Documentation

- [Workflow](docs/WORKFLOW.md): what each recommendation step does.
- [Architecture](docs/ARCHITECTURE.md): files, folders, and data flow.
- [Scoring](docs/SCORING.md): quality, feedback, calibration, and mathematics.
- [Reference](docs/REFERENCE.md): commands, tests, distribution, dependencies, and copyright.

Deepvids uses the [MIT license](LICENSE).
