# Terminal and maintenance reference

Use the [README](../README.md) for the first installation.
This reference explains individual functions, tests, distribution, and dependencies.
Examples use PowerShell in the source folder unless stated otherwise.

## Read command examples

Start each source command with `uv run --no-sync deepvids`.
This prefix runs the installed project engine through UV.
After a Node package installation, the equivalent prefix is `deepvids`.
For an unpacked Node package, use `node C:\path\to\deepvids\bin\deepvids.cjs`.

Replace `VIDEO_URL`, `RUN_ID`, `BATCH_ID`, and file paths with your values.
A run ID identifies one saved job.
A batch ID identifies the combined YouTube and Bilibili job.
The command output prints these IDs.

The global `--data-dir` argument must precede the command.
For example:

```powershell
uv run --no-sync deepvids --data-dir 'C:\Deepvids Data' profile show
```

Use `--help` after a command for its accepted arguments:

```powershell
uv run --no-sync deepvids --help
uv run --no-sync deepvids recommend --help
uv run --no-sync deepvids evidence prepare --help
```

## Choose a data directory

The default Windows directory is `%LOCALAPPDATA%\deepvids`.
Other systems default to `~/.local/share/deepvids` when `LOCALAPPDATA` is absent.
All commands for one profile must use the same directory.
Changing directories does not transfer existing data.

For the current PowerShell session, select a directory with:

```powershell
$env:DEEPVIDS_DATA_DIR = 'C:\Deepvids Data'
```

An explicit `--data-dir` overrides this environment setting.
Keep the data directory outside an installed plugin or npm package.
The Node launcher also stores its engine environment there.

## Prepare tools and sign-in

```powershell
uv sync --locked --extra evidence --extra asr
uv run --no-sync deepvids doctor
codex login
uv run --no-sync deepvids login bilibili
```

| Option or command | Meaning |
| --- | --- |
| `--extra evidence` | Install browser, download, and image tools |
| `--extra asr` | Install optional speech transcription tools |
| `doctor` | Report local tools without checking account authentication |
| `login bilibili` | Open a separate browser for manual sign-in |
| `login bilibili --confirm` | Request verification from the open sign-in window |
| `login bilibili --timeout 600` | Allow 600 seconds for manual sign-in |

Complete sign-in in the window before you run `login bilibili --confirm`.
Keep its original terminal open during sign-in.
Use another terminal for the confirmation command.
Do not paste cookies or passwords into a command.
If a security challenge blocks access, stop that operation.

If Edge and Chrome are absent, install the Playwright browser:

```powershell
uv run --no-sync playwright install chromium
```

## Set interests

```powershell
uv run --no-sync deepvids profile show
uv run --no-sync deepvids profile set --interest mathematics --interest travel
```

`profile set` replaces the explicit interest list.
Repeat `--interest` for every subject you want to retain.
It does not infer interests from your watch history.

## Request recommendations

```powershell
uv run --no-sync deepvids recommend both --use-browser --asr-model base
```

Use `youtube` or `bilibili` instead of `both` for one platform.
A new command requests a new round.
It retains existing feedback and pending recommendations.

| Argument | Meaning | Default |
| --- | --- | --- |
| `--use-browser` | Use the separate Bilibili login for metadata and evidence | Disabled |
| `--max-seconds` | Time allowance for the recommendation invocation | 1800 seconds |
| `--workers` | Maximum shared model-worker count | 4 |
| `--effort` | Model reasoning effort, `low` or `medium` | `low` |
| `--max-requests` | Metadata request allowance per platform | 160 |
| `--max-assessments` | Total model-call allowance per platform, including planning | 52 |
| `--max-mib` | New media allowance per platform, in mebibytes | 1024 |
| `--asr-model` | Local transcription model, `tiny`, `base`, or `small` | No transcription fallback |
| `--discovery-run` | Reuse a saved discovery buffer for a single platform | Collect fresh candidates |

The combined batch shares one thirty-minute allowance and its worker slots.
Its platform jobs retain separate request, media, and model allowances.
Discovery requests use a separate bounded counter within the shared time allowance.
Model workers use GPT-6.1 Sol through the authenticated Codex CLI.
Failed calls consume their allowance.
The engine does not switch to paid API access automatically.

## Continue an incomplete run

```powershell
uv run --no-sync deepvids status
uv run --no-sync deepvids continue BATCH_ID
uv run --no-sync deepvids report RUN_ID
```

Use the batch ID for a combined recommendation run.
Its platform child jobs cannot continue independently.
Continuation preserves the plan, elapsed time, and remaining resource allowances.
It does not create a fresh allowance.
Resolve the reported access problem before another attempt.

`report` writes local JSON and Markdown files.
The `publish` command also writes local results.
It does not upload a website or package.

```powershell
uv run --no-sync deepvids publish RUN_ID --allow-incomplete
```

Use `--allow-incomplete` only when you want a partial list from the assessed subset.
The report identifies that result as partial.

## Review and rate videos

```powershell
uv run --no-sync deepvids serve
```

Open `http://127.0.0.1:8765`.
`serve --port 9000` selects another port.
The server accepts local connections only.
Press Ctrl+C in its terminal to stop it.
Closing the browser tab does not stop the server.

For a background server, identify the listener:

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen
```

Check the process command line before you stop that process.
Stop its launcher too if the launcher remains active.
Server shutdown preserves saved results and feedback.

Use terminal feedback when you prefer commands:

```powershell
uv run --no-sync deepvids video watched VIDEO_URL
uv run --no-sync deepvids video dismiss VIDEO_URL
uv run --no-sync deepvids rate VIDEO_URL topic_interest low --target topology
uv run --no-sync deepvids rate VIDEO_URL domain_interest high --target mathematics
uv run --no-sync deepvids rate VIDEO_URL depth deeper --target topology --level introductory
uv run --no-sync deepvids rate VIDEO_URL quality_level medium --note 'Useful but repetitive'
```

| Field | Meaning |
| --- | --- |
| `topic_interest` | Interest in one specific topic |
| `domain_interest` | Interest in one broad subject |
| `quality_level` | Quality of this video |
| `depth` | Preferred treatment depth for the named topic |
| `--target` | Topic or subject that the judgment concerns |
| `--level` | Observed video level, not your preferred level |
| `--note` | Optional comment that does not create additional votes |

Topic, subject, and quality accept `high`, `medium`, or `low`.
Depth accepts `deeper`, `suitable`, or `lighter`.
Use `deeper` when the video is too elementary.
Use `lighter` when the video is too advanced.
Audience levels are `unknown`, `introductory`, `intermediate`, and `advanced`.
Unknown levels do not transfer a depth adjustment to later videos.

Clear one dimension with:

```powershell
uv run --no-sync deepvids rate VIDEO_URL topic_interest clear
```

`video unwatched` reverses watched status.
`video restore` reverses dismissal.
These actions do not clear ratings.
Use `queue` to inspect saved state:

```powershell
uv run --no-sync deepvids queue --platform bilibili
```

Earlier binary feedback remains available:

```powershell
uv run --no-sync deepvids video up VIDEO_URL --kind quality --reason 'Clear explanation'
uv run --no-sync deepvids video clear VIDEO_URL --kind quality
```

`--kind` accepts `interest`, `quality`, or `overall`.
Its default is `overall`.
Structured clarifications can replace these votes during fitting without deleting their history.

## Calibration and evidence maintenance

```powershell
uv run --no-sync deepvids calibrate start
uv run --no-sync deepvids calibrate list
uv run --no-sync deepvids learn status
uv run --no-sync deepvids learn fit
uv run --no-sync deepvids learn evaluate
```

`calibrate start` selects thirty eligible, previously unrated videos from existing verified evidence.
It does not discover or assess replacement videos automatically.
`calibrate start --new` requests another cohort after the current reservation ends.
`learn fit` rebuilds the current model.
`learn evaluate` calculates or retrieves the applicable held-out result.
See [calibration and mathematics](SCORING.md).

Refresh one video's metadata without media downloads or model calls:

```powershell
uv run --no-sync deepvids metadata VIDEO_URL --use-browser
```

Repair rated evidence that lacks current source information:

```powershell
uv run --no-sync deepvids learn repair-evidence --max-seconds 180 --max-videos 1
```

This operation can refresh metadata and call the model with retained captions and frames.
It preserves ratings and original scorecards.
It starts no new media download.
Its limits are one to three videos and at most 600 seconds.

## Inspect discovery and evidence

```powershell
uv run --no-sync deepvids topics
uv run --no-sync deepvids discover youtube --seed 42
uv run --no-sync deepvids discover bilibili --curated
uv run --no-sync deepvids queries --platform youtube --export my-queries.json
uv run --no-sync deepvids discover youtube --bank my-queries.json
```

| Argument | Meaning |
| --- | --- |
| `--seed` | Repeatable random seed for topic selection |
| `--curated` | Use the curated query bank instead of hierarchical planning |
| `--bank` | Use the specified query-bank JSON file |
| `--round` | Select a curated-bank round number |
| `--max-planning-calls` | Limit model calls for query planning, default 2 |
| `--creators` | Limit creator expansion sources, default 4 |
| `--creator-pages` | Limit pages per creator expansion, default 2 |

Standalone discovery defaults to 100 requests and 600 seconds.
Its shortlist is a candidate preview, not a quality-ranked recommendation list.

Prepare or assess one playable video:

```powershell
uv run --no-sync deepvids evidence prepare VIDEO_URL --use-browser --asr-model base
uv run --no-sync deepvids evidence assess RUN_ID --mode augmented
uv run --no-sync deepvids evidence captions RUN_ID
uv run --no-sync deepvids evidence frame RUN_ID --at 120 --reason 'Check the diagram'
```

| Argument | Meaning |
| --- | --- |
| `--mode baseline` | Assess captions without frames |
| `--mode augmented` | Assess captions and selected frames |
| `--mode both` | Run both assessments, the default for the standalone command |
| `--media` | Use an explicit local video file |
| `--captions` | Use explicit JSON3, JSON, VTT, or SRT captions |
| `--at` | Timestamp in seconds for an additional frame |
| `--long-edge` | Maximum output frame dimension, default 768 pixels |

Use `--use-browser` for Bilibili access.
Include the requested Bilibili part as `?p=1` or its actual part number.
Standalone preparation defaults to 128 MiB and 1200 seconds.
An evidence job allows two model calls and eight submitted images.
Add a frame before the augmented assessment, within that limit.
`evidence pilot-report` compares saved caption-only and frame-assisted assessments.

## Clean temporary evidence

```powershell
uv run --no-sync deepvids cleanup
uv run --no-sync deepvids cleanup --apply
```

The first command previews disposable files.
The second command applies the cleanup policy.
The policy preserves user originals, active jobs, and user history.
Completed assessments normally remove downloaded full videos automatically.

## Run automated checks

```powershell
uv sync --locked --extra evidence --extra asr
npm ci --ignore-scripts
uv run --no-sync ruff check src tests scripts
uv run --no-sync ruff format --check src tests scripts
uv run --no-sync python -m unittest discover -s tests
npm test
npm run check:web
```

Tests use temporary data and controlled responses.
Optional media tests need their dependencies.
Unit tests do not require platform sign-in or subscription model calls.
`gate` reports development readiness from saved local records.
It does not replace a clean installed-plugin test.

## Build and distribute

Build local artifacts:

```powershell
uv build --wheel --out-dir python
npm pack --ignore-scripts
uv run --no-sync scripts/verify_package.py
```

UV builds the Python wheel.
npm creates the local package archive.
The package check compares source files, versions, links, and private-file exclusions.
These commands do not upload artifacts.

From an unpacked package, install its external engine:

```powershell
node bin/deepvids.cjs setup --asr
node bin/deepvids.cjs doctor
```

`setup` installs the dependency-free core.
`setup --evidence` also installs browser and media tools.
`setup --asr` includes those tools and transcription dependencies.
Setup requires Node.js 20 or later and UV.
It does not use an npm postinstall script.

Actual Codex plugin installation remains a separate pending host check.
The intended local installation uses a repo marketplace and the desktop Plugins Directory.
See the [official local plugin guide](https://developers.openai.com/plugins/build/plugins).
Daily scheduling also remains unimplemented.

Public release has three separate destinations:

| Destination | Artifact | Release action |
| --- | --- | --- |
| GitHub | Reviewed source, skills, manifests, documentation, and synthetic examples | Push the approved commit or release |
| npm | Tested launcher, wheel, plugin files, and dependency locks | Publish the approved package version |
| OpenAI Plugins Directory | Desktop-only skill package ZIP and listing material | Submit, resolve review findings, and publish after approval |

Approve the exact destination and files before any public upload.
Use synthetic state for examples and screenshots.
Exclude real preferences, ratings, history, sessions, media, captions, frames, logs, and backups.
Git ignores the internal plan, local environments, state, and build archives.
The npm file list includes only the selected public files.

A public listing does not host the dashboard or upload user recommendations.
OpenAI acceptance and account eligibility require external review.
See [desktop-only submission](https://developers.openai.com/plugins/deploy/desktop-only) and [publication steps](https://developers.openai.com/plugins/deploy/submission).
No npm release or public-directory listing exists for this preview.

## Tools, dependencies, and copyright

Deepvids uses the [MIT license](../LICENSE).
The project notice names copyright holder **antidemosthenes**, year **2026**.
Keep that notice with redistributed Deepvids code.

Third-party authors retain their own copyright.
The following table identifies the main tools and their upstream license information.
It is not a complete list of optional transitive packages.

| Tool | Use | Upstream license or terms |
| --- | --- | --- |
| [Python](https://docs.python.org/3/license.html) | Engine runtime | PSF license and included notices |
| [UV](https://docs.astral.sh/uv/reference/policies/license/) | Python environments and packages | MIT or Apache-2.0 |
| [Node.js](https://github.com/nodejs/node/blob/main/LICENSE) | Launcher runtime | MIT and included third-party notices |
| [Codex CLI](https://developers.openai.com/codex/cli/) | Authenticated model execution | See the CLI distribution and OpenAI service terms |
| [SQLite](https://sqlite.org/copyright.html) | Local database through Python | Public domain dedication |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp#license) | Playable metadata, captions, and media | Unlicense for core source, with separate dependency notices |
| [Playwright](https://github.com/microsoft/playwright-python/blob/main/LICENSE) | Separate browser profile | Apache-2.0 |
| [Pillow](https://pillow.readthedocs.io/en/stable/about.html#license) | Frame image processing | MIT-CMU |
| [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg/blob/main/LICENSE) | FFmpeg executable access | BSD-2-Clause for the wrapper |
| [FFmpeg](https://ffmpeg.org/legal.html) | Media decoding and frame extraction | LGPL or GPL, depending on the binary build |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE) | Optional speech transcription | MIT |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2/blob/master/LICENSE) | Transcription inference | MIT |
| [PyAV](https://github.com/PyAV-Org/PyAV/blob/main/LICENSE.txt) | Transcription media access | BSD-3-Clause, with separate FFmpeg terms |
| [Hugging Face Hub](https://github.com/huggingface/huggingface_hub/blob/main/LICENSE) | Optional model download | Apache-2.0 |
| [Hatchling](https://github.com/pypa/hatch/blob/master/LICENSE.txt) | Python wheel build | MIT |
| [Ruff](https://github.com/astral-sh/ruff/blob/main/LICENSE) | Python formatting and checks | MIT |
| [PyYAML](https://github.com/yaml/pyyaml/blob/main/LICENSE) | Development skill checks | MIT |
| [Prettier](https://github.com/prettier/prettier/blob/main/LICENSE) | Browser asset formatting | MIT |

The Node archive contains the Deepvids wheel and dependency lock files.
It does not bundle the optional Python distributions, browser binaries, or model weights.
Explicit setup downloads selected dependencies separately.
FFmpeg binaries and model weights can have terms distinct from their Python wrappers.
Retain the notices supplied with the exact versions that you redistribute.

`uv.lock` and `package-lock.json` record resolved dependency versions.
The two requirements files under `python/` also record package hashes.
Check the installed distributions' license files when preparing a release.
The Deepvids license does not grant rights to platform videos or third-party media.
