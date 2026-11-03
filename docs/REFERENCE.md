# Terminal and maintenance reference

Use the [README](../README.md) for the first installation.
This reference explains commands, local development, distribution, and dependencies.
Examples use PowerShell in the source folder unless stated otherwise.

## Read command examples

Start each source command with `uv run --no-sync broader-vids`.
This prefix runs the installed project engine through UV.
After a Node package installation, the equivalent prefix is `broader-vids`.
For an unpacked Node package, use `node C:\path\to\broader-vids\bin\broader-vids.cjs`.

Replace `VIDEO_URL`, `RUN_ID`, `BATCH_ID`, and file paths with your values.
A run ID identifies one saved job.
A batch ID identifies the combined YouTube and Bilibili job.
The command output prints these IDs.

The global `--data-dir` argument must precede the command.
For example:

```powershell
uv run --no-sync broader-vids --data-dir 'C:\Broader Vids Data' profile show
```

Use `--help` after a command for its accepted arguments:

```powershell
uv run --no-sync broader-vids --help
uv run --no-sync broader-vids recommend --help
uv run --no-sync broader-vids evidence prepare --help
```

## Choose a data directory

The default Windows directory is `%LOCALAPPDATA%\broader-vids`.
Other systems default to `~/.local/share/broader-vids` when `LOCALAPPDATA` is absent.
All commands for one profile must use the same directory.
Changing directories does not transfer existing data.

For the current PowerShell session, select a directory with:

```powershell
$env:BROADER_VIDS_DATA_DIR = 'C:\Broader Vids Data'
```

An explicit `--data-dir` overrides this environment setting.
Keep the data directory outside an installed plugin or npm package.
The host can redirect `LOCALAPPDATA`, so setup always reports the actual absolute path.
An ordinary terminal can resolve a different default from a sandboxed Codex conversation.
Use the reported directory explicitly to access the same profile.
Plugin uninstall preserves external profile data.
Broader Vids supports sandbox profiles. Use an explicit path to select the same profile across environments.
The Node launcher also stores its engine environment there.

## Prepare tools and sign-in

```powershell
uv sync --locked --extra evidence
uv run --no-sync broader-vids doctor
codex login
uv run --no-sync broader-vids login bilibili
```

| Option or command | Meaning |
| --- | --- |
| `--extra evidence` | Install browser, download, and image tools |
| `--extra asr` | Install optional speech transcription tools |
| `doctor` | Report local tools without checking account authentication |
| `login bilibili` | Open a separate browser for manual sign-in |
| `login bilibili --confirm` | Request verification from the open sign-in window |
| `login bilibili --timeout 600` | Allow 600 seconds for manual sign-in |

Sign in manually in the separate Bilibili window.
Keep the window open while you return to chat to report completion.
For terminal use, run `login bilibili --confirm` in a second terminal.
Broader Vids verifies the session and closes the window when sign-in checks finish.
If you close it before verification, start sign-in again.

### Required and optional setup items

`doctor` separates local requirements from optional tools and access checks.

| Group | Items | Effect when unavailable |
| --- | --- | --- |
| Required for setup and recommendations | Python 3.12 or later, Node.js 20 or later, UV, Codex CLI, FFmpeg, yt-dlp, Pillow, and OpenCC | The local check fails and lists the required items to install or repair |
| Conditional for Bilibili browser access | Playwright and Edge, Chrome, or Playwright Chromium | Browser sign-in and authenticated Bilibili access need these items |
| Conditional for full Bilibili video access | Verified Bilibili sign-in | Public discovery remains separate from full-video access |
| Optional | faster-whisper | Native captions still work, but transcription fallback is unavailable |
| Optional | ffprobe | Broader Vids duration and frame checks still work through FFmpeg |
| Manual checks | ChatGPT sign-in and internet access | AI assessment and remote access require separate verification |

The local check verifies the Node.js version and runs the selected FFmpeg executable.
FFmpeg can come from PATH or the bundled imageio-ffmpeg executable.
Installing imageio-ffmpeg does not install ffprobe.
Module checks report installed packages rather than a complete media-processing test.
The browser check detects common system browsers without starting them.
It does not inspect cached Playwright Chromium.

`local_requirements_met` describes required local items only.
An exit code of zero means those local checks passed.
An exit code of two means a required item is missing, unusable, or unsupported.
Missing optional items do not change that exit code.
`not_checked` means unverified rather than missing.
The command does not read credentials, open a browser, or test network access.
The setup skill reports required failures, conditional limitations, and optional omissions separately.

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

In the dashboard, select **Preferences**.
The editor shows your current interests with or without an import.
Enter one broader interest per line, then select **Save**.
YouTube and Bilibili share this list.
Use 1–40 interests, with at most 80 characters each.
Saving preserves ratings, learned calibration, exclusions, and video history.
Finish active recommendation work before saving.
After a change, start a new round instead of continuing a paused round with an older profile.
Unsaved interests stay in the current tab when you leave Preferences.
Select **Reload saved interests** to discard that draft and load the current saved list.
If another window changes the interests, reload the saved list before another save.
Unrelated exclusion edits do not discard or block an interest draft.

The terminal provides the same interest update:

```powershell
uv run --no-sync broader-vids profile show
uv run --no-sync broader-vids profile set --interest mathematics --interest travel
uv run --no-sync broader-vids profile preset starter
uv run --no-sync broader-vids profile preset starter --apply
```

`profile set` replaces the explicit interest list.
Repeat `--interest` for every subject you want to retain.
It does not infer interests from your watch history.
The starter preset command previews its changes by default.
Add `--apply` to import the preset and create the import backup.
The preset includes sanitized interests, parameters, and parameter uncertainty.
It excludes watched IDs, individual history, topic coverage, and creator exclusions.

## Export or import learned preferences

In the dashboard, select **Preferences**.
Imported interests appear in the same page editor. Edit them and select **Save** when needed.
Save interest edits before exporting.
Select **Export preferences** to download a JSON file.
For import, select **Choose file**.
Broader Vids checks the file and shows its summary automatically.
Select **Import preferences** to apply it.
If preferences changed after the preview, check the refreshed summary before you select **Import preferences** again.

The terminal provides the same operations:

```powershell
uv run --no-sync broader-vids profile export 'C:\Broader Vids Data\preferences.json'
uv run --no-sync broader-vids profile import 'C:\Broader Vids Data\preferences.json'
uv run --no-sync broader-vids profile import 'C:\Broader Vids Data\preferences.json' --apply
```

The import command previews the file unless you include `--apply`.
Export refuses to overwrite an existing file unless you include `--overwrite`.
Files must use the `.json` extension and fit within 8 MiB.
Import checks the scoring version, checksum, and parameter uncertainty.
It rejects incompatible or invalid files before changing preferences.

Format 4 uses learning model version 8 and feature version 1.
It transfers interests, learned parameters and their uncertainty, watched video IDs, a bounded exploration summary, and manual creator exclusions.
Imports mark matching IDs as watched to prevent exact video repeats.
Imports of other formats or model versions fail before they change preferences.
The exploration summary contains at most 1,024 hierarchy nodes, aggregate counts, and relative round ages.
It contains no individual video history.
Files exclude titles, individual ratings, calibration examples, and recommendation history.
They exclude tag rules, account-specific blacklist copies, account data, cookies, media, and dashboard settings.
Manual creator rules use platform-specific stable IDs and names, with at most 10,000 rules per file.
Import replaces both local manual creator lists with the lists in the file.
An empty imported list clears that platform's manual rules.
Account-specific blacklist entries remain unchanged.
Blacklist sync updates only the verified account's imported copy. It preserves manual rules.
Coverage imports keep local parent relationships and adjust new child depths to match.
Imports reject topic conflicts and a merged hierarchy above 1,024 topics.
Repeated imports retain coverage age instead of restarting its cooldown.
Imports preserve local video lists, ratings, watch states, tag rules, and account-specific blacklist entries.
They add watched status for imported IDs without clearing local states.
Existing local ratings stay visible but do not enter the imported model again.
New or edited ratings continue learning from the imported parameters.

Import saves the current parameters in `backups/preferences-<identifier>.json` under the data directory.
Use that file with the same import operation to restore the earlier parameters.
This recovery file contains watched video IDs and prior manual creator rules, but no titles or full video history.
Restoring it also restores the prior manual creator lists. Account-specific blacklist copies remain unchanged.
Import does not reconstruct or replace a dashboard list.

Exports use atomic file publication and leave existing files intact unless overwrite is explicit.
An exact cached model avoids another fitting operation during export.
Import prepares its model before the database write transaction.
If feedback changes during preparation, the replacement stops and preserves that feedback.
Failed imports remove unused recovery files. Successful imports retain recovery files for later restoration.
The transfer dialog releases its selected file when you close it.
Rating drafts remain intact.

## Reset or remove profile data

Use the installed Reset or Clean skill for the guided workflow.
Their identifiers are `$broader-vids-reset` and `$broader-vids-clean`.
The host can show plugin-qualified names.
These commands are separate from `cleanup`, which removes disposable evidence and preserves preferences.

| Action | Removes | Preserves |
| --- | --- | --- |
| Reset | Preferences, learned weights, ratings, creator and tag rules, histories, results, evidence, and recovery files | Installed runtime, transcription models, Bilibili sign-in, selected directory, and unknown files |
| Clean | Complete managed data directory, including tools and local sign-in | Installed plugin, exports outside the directory, and shared UV or npm caches |

Built-in screening rules remain part of the installed code.
Neither action changes your remote Bilibili blacklist.
A later sync can import that blacklist again.
Run setup after either action.

The following source-checkout commands preview the selected profile without changing it:

```powershell
node bin/broader-vids.cjs data show
node bin/broader-vids.cjs reset
node bin/broader-vids.cjs clean
```

Use the absolute launcher path from setup when running an installed plugin.
Run these commands from outside the selected data directory.
A source checkout also supports `uv run --no-sync broader-vids reset` and `clean`.
Both routes delegate destructive file operations to Node.
This lets Clean remove its Python runtime after that runtime exits.

Check the printed absolute target, estimated size, removed items, retained items, and blockers.
Finish active runs and sign-in. Stop this profile's dashboard.
An explicit apply requires the same absolute directory in `--confirm-path`:

```powershell
node bin/broader-vids.cjs --data-dir 'C:\exact\profile' dashboard stop
node bin/broader-vids.cjs --data-dir 'C:\exact\profile' reset --apply --confirm-path 'C:\exact\profile'
node bin/broader-vids.cjs --data-dir 'C:\exact\profile' clean --apply --confirm-path 'C:\exact\profile'
```

Choose Reset or Clean. Do not run both unless that is your intent.
An explanatory or implementation request does not authorize deletion of the real profile.
Unknown files block Clean, and Reset preserves them.
The commands reject home directories, system roots, plugin directories, and unrecognized profiles.
They do not follow directory links outside the selected data.
Active or uncertain locks block both actions.
A failed reset staging move restores the files before deletion starts.
An interrupted deletion can retain `.reset-pending` for recovery.
Complete Reset or Clean before using that profile again.
A partial result means some data can remain.
The commands do not create an archive that retains the removed preferences.
Keep an export outside the target directory if you want to restore its learned parameters later.

## Guided setup and preflight

The installed setup skill introduces the plugin and records your platform choice.
An existing profile without onboarding metadata receives this introduction once.
Its interests, ratings, and learned weights remain intact.

```powershell
uv run --no-sync broader-vids onboarding status
uv run --no-sync broader-vids onboarding questions
uv run --no-sync broader-vids onboarding configure --platform both
uv run --no-sync broader-vids onboarding complete --format chat --skill-namespace broader-vids
uv run --no-sync broader-vids preflight
```

`onboarding complete --format chat` returns the complete setup guide after a successful check.
Its output includes the selected platforms, data directory, optional warnings, and four next actions.
Omit `--format chat` for the existing JSON report.
Use `--skill-namespace broader-vids` when the host exposes commands such as `$broader-vids:broader-vids-dashboard`.
Omit the namespace for standalone skills.
Blocked completion still returns structured diagnostics and a nonzero exit code.

`onboarding questions` returns the shared question titles, input types, and choice values for the chat host.
It does not change preferences, configure platforms, open a browser, or run access checks.
The setup skill uses those definitions with an available user-question tool and waits for each answer.
Free-text questions collect interests, an import-file path, or sign-in troubleshooting details.
A host without suitable question controls uses text questions.
Configure the platform before completion.
Import preferences or set interests before completion.
Complete Bilibili sign-in when the selected platforms include Bilibili.
Completion verifies the requirements before it records success.
Include `--asr-model base` in `onboarding complete` only when you requested transcription.
The check verifies the transcription package without downloading a model.
A successful response includes `self_check: passed`, the actual data directory, and `next_steps`.
Each next action includes a chat request, skill name, and skill invocation.
Skill replies keep raw launcher commands internal.
The host supplies the exact skill invocation syntax.
For terminal use, supply the model and effort as described in the recommendation command reference.
Changing the default platform requires completion again.
Reusing the same choice preserves completion.

`preflight [youtube|bilibili|both]` checks readiness without creating a recommendation round.
Omitting the platform uses the saved default.
Use `--asr-model base` to check requested transcription support.
Pass both `--model` and `--effort` from the host when chat inheritance is unavailable.
The report contains checks, remedies, optional warnings, platform counts, and elapsed seconds.
Statuses are `ready`, `setup_required`, `blocked`, and `feedback_required`.
Only `ready` returns exit code zero. The other statuses return two.

Unlike `doctor`, preflight checks ChatGPT sign-in and starts the saved Bilibili browser when needed.
It imports required media modules and checks the selected FFmpeg executable.
It does not call a quality model or synchronize the blacklist.
Automated subprocess checks have a maximum thirty-second deadline.
They use the existing owned-process cleanup.
A timed-out browser check removes only its own browser lock.
A busy browser or recommendation operation requires a later retry.
Do not remove another operation's lock.

Any blank rating dimension makes a recommended video incomplete.
Counts include all previous recommendation exposures for the requested platforms, with duplicate videos counted once.
Dismissed videos and calibration-only examples do not count.
The report separates videos without ratings from partially rated videos.
Choose **Open dashboard and stop** to review feedback.
Request recommendations again after review.
Alternatively, explicitly choose **Continue this round**.
The terminal equivalent is `recommend --allow-missing-feedback`.
That flag does not fill blank ratings or disable later reminders.

Required failures always block work, including when that flag is present.
For both platforms, a failure does not silently switch to one platform.
Existing blacklist-sync failures remain nonblocking under the saved-copy policy.
Resuming uses saved settings and checks readiness without the feedback prompt.
Automated checks count toward an explicit total allowance when one exists.
Crash recovery and preflight count the same elapsed time only once.
Setup, manual repairs, and user decisions occur outside that allowance.

## Request recommendations

```powershell
uv run --no-sync broader-vids recommend both --use-browser --asr-model base
```

Use `youtube` or `bilibili` instead of `both` for one platform.
Omit the platform to use the saved onboarding choice.
An explicit platform applies to this request without changing the saved default.
A new command requests a new round.
It retains existing feedback and pending recommendations.
`pending_now` counts published recommendations that are still unwatched and undismissed, including entries in the current list.

| Argument | Meaning | Default |
| --- | --- | --- |
| `--use-browser` | Use the separate Bilibili login for metadata and evidence | Automatic for new Bilibili rounds |
| `--allow-missing-feedback` | Continue this round after accepting the feedback reminder | Disabled |
| `--max-seconds` | Optional total time allowance in seconds | No overall deadline |
| `--workers` | Maximum shared model-worker count | 4 |
| `--model` | Explicit host bridge for the invoking chat's model | Inherit current chat |
| `--effort` | Explicit host bridge for its reasoning effort | Inherit current chat |
| `--max-requests` | Metadata request allowance per platform, including blacklist sync and collection refills | 320 |
| `--max-assessments` | Total model-call allowance per platform, including planning | 52 |
| `--max-mib` | New media allowance per platform, in mebibytes | 1024 |
| `--asr-model` | Local transcription model, `tiny`, `base`, or `small` | No transcription fallback |
| `--discovery-run` | Reuse a saved discovery buffer for a single platform | Collect fresh candidates |

The combined batch shares worker slots and has no overall time limit by default.
You can set an optional shared limit with `--max-seconds`.
The limit can exceed 1,800 seconds.
Its platform jobs retain separate request, media, and model allowances.
Discovery requests use a separate bounded counter and a ten-minute stage timeout.
Legacy checkpoints with the former default 1800-second cap continue without that cap.
Explicit limits saved by this version remain effective during continuation.
Stored elapsed time and completed work remain intact.
Planning and assessment workers inherit the invoking Codex chat's model and reasoning effort through the authenticated Codex CLI.
The engine resolves only the active thread's `turn_context` model and effort, then saves the pair for the run.
Resuming retains that pair.
A new invocation reads the current selection again.
If local turn metadata is unavailable, pass both `--model` and `--effort` from the host selection.
The engine does not silently substitute global CLI defaults or a preset model.
Accepted effort names are `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`.
The selected model and CLI must support the requested effort.
Failed calls consume their allowance.
The engine does not switch to paid API access automatically.

## Continue an incomplete run

Continue uses the saved request cap unless you explicitly increase it.
For a metadata-budget pause, use `continue RUN_ID --max-requests 320` or a larger cap up to 1,000.
For a two-platform round, use its batch ID.
The update applies to unfinished platform checkpoints and leaves completed results intact.
The new value is a total cap, not an additional request count.
Prior requests, elapsed time, collected candidates, model settings, and exclusion snapshots do not reset.
Active workers block the update.
The readiness check must pass before the update applies.
A request-cap increase cannot repair exhausted time or an access challenge.


```powershell
uv run --no-sync broader-vids status
uv run --no-sync broader-vids continue BATCH_ID
uv run --no-sync broader-vids report RUN_ID
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
uv run --no-sync broader-vids publish RUN_ID --allow-incomplete
```

Use `--allow-incomplete` only when you want a partial list from the assessed subset.
The report identifies that result as partial.

## Review and rate videos

```powershell
uv run --no-sync broader-vids serve
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
uv run --no-sync broader-vids video watched VIDEO_URL
uv run --no-sync broader-vids video dismiss VIDEO_URL
uv run --no-sync broader-vids rate VIDEO_URL topic_interest low --target topology
uv run --no-sync broader-vids rate VIDEO_URL domain_interest high --target mathematics
uv run --no-sync broader-vids rate VIDEO_URL depth deeper --target topology --level introductory
uv run --no-sync broader-vids rate VIDEO_URL quality_level medium
```

| Field | Meaning |
| --- | --- |
| `topic_interest` | Interest in one specific topic |
| `domain_interest` | Interest in one broad subject |
| `quality_level` | Quality of this video |
| `depth` | Preferred treatment depth for the named topic |
| `--target` | Topic or subject that the judgment concerns |
| `--level` | Observed video level, not your preferred level |

Topic, subject, and quality accept `high`, `medium`, or `low`.
Depth accepts `deeper`, `suitable`, or `lighter`.
Use `deeper` when the video is too elementary.
Use `lighter` when the video is too advanced.
Audience levels are `unknown`, `introductory`, `intermediate`, and `advanced`.
Unknown levels do not transfer a depth adjustment to later videos.

Clear one dimension with:

```powershell
uv run --no-sync broader-vids rate VIDEO_URL topic_interest clear
```

`video unwatched` reverses watched status.
`video restore` reverses dismissal.
These actions do not clear ratings.
Use `queue` to inspect saved state:

```powershell
uv run --no-sync broader-vids queue --platform bilibili
```

The current interface omits binary vote commands.
Use the separate rating dimensions instead.
Each row has one Save control for its rating.
The Tag exclusion rules panel shows separately saved exclusions.

## Creator and tag exclusions

List all local exclusion rules:

```powershell
uv run --no-sync broader-vids exclusions list
```

Exclude the creator of a known video:

```powershell
uv run --no-sync broader-vids exclusions video add VIDEO_URL
uv run --no-sync broader-vids exclusions video remove VIDEO_URL
```

These commands use saved creator metadata.
Refresh the video's metadata first if its creator ID is unknown.
You can also use stable creator IDs directly:

```powershell
uv run --no-sync broader-vids exclusions creator add youtube CHANNEL_ID
uv run --no-sync broader-vids exclusions creator remove youtube CHANNEL_ID
uv run --no-sync broader-vids exclusions creator add bilibili ACCOUNT_ID
uv run --no-sync broader-vids exclusions tag add bilibili TAG
uv run --no-sync broader-vids exclusions tag remove bilibili TAG
uv run --no-sync broader-vids exclusions sync-bilibili
```

Replace CHANNEL_ID with the complete YouTube channel ID that starts with UC.
Replace ACCOUNT_ID with the creator's positive numeric Bilibili ID.
Replace TAG with an exact tag name.
The engine includes the 视频翻译 exclusion by default.
You can remove this exclusion.
Remove it under Exclusions → Bilibili → Tags, or use the terminal remove command.
Use Add tag or the terminal add command to restore it.
A removal persists across restarts, interest changes, and preference imports.
The tag still supplies originality evidence when its exclusion is off.
Imported creator rules cannot be overridden locally.
A manual removal leaves any imported ban in effect.
The manual list is separate for each platform.

Sync uses the isolated browser session without changing Bilibili account settings.
It commits only a complete import and keeps manual rules.
Each attempt permits sixty seconds and one hundred requests.
Automatic attempts share the recommendation run's existing allowance.
Failures preserve the last complete copy and report its age.
Creator rules use their own revision and do not retrain the preference model.
The dashboard checkbox saves independently of ratings and affects the next new round.
Each platform also has an Add tag form for exact metadata tags.
YouTube rules match creator-supplied keyword tags when the video metadata includes them.
These tags differ from visible #hashtags.
Repeated tag entries with different letter case do not create duplicate rules.
The Exclusions view shows rule sources and sync status.

The packaged dashboard skill opens the local dashboard through the host's native skill invocation.
Recommendations appear directly in chat.
The dashboard order is Recommendations, Exclusions, then optional Calibration.

### Use the dashboard

1. Select Recommendations in the navigation.
2. Select YouTube or Bilibili above the list.
3. Select Rate beside a video.
4. Check the topic labels and video level.
5. Select a rating in each applicable group.
6. Select Save within that group.

Clear removes that group's saved judgment.
Depth for this video keeps the existing topic-scoped learning behavior.
Video level records your judgment of the observed difficulty.
Drafts survive video, platform, and section changes within the open tab.
Save drafts before you close or reload the page.
Failed writes retain the entered values and show a local error.

Mark watched and Dismiss are separate actions beside each video.
Restore reverses dismissal.
Earlier unwatched opens pending videos from earlier lists when available.
Current picks returns to the latest recommendation list.
Score details opens technical information without expanding list rows.
Help explains the feedback definitions.

Select Exclusions to manage Creators or Tags for the selected platform.
The Bilibili connection panel separates sync status, account identity, and the last successful sync.
Times use your browser's local time zone.
The timestamp tooltip includes seconds and the time zone.
Sync blacklist updates only that operation's busy state.
Other navigation remains available.

To change accounts, select Switch account under Exclusions → Bilibili.
Sign in manually in the new browser window.
Keep it open, return to the dashboard, and select I've signed in.
Broader Vids closes the window when sign-in checks finish.
The current session stays active until the new session passes verification.
Cancel switch, a timeout, or failed verification keeps the current session.
After verification, the dashboard activates the new session and removes the previous local session.
This does not sign you out on other devices.
Preferences, ratings, histories, and manual exclusions stay unchanged.
The new account uses its own imported blacklist.
If sync fails, its saved blacklist stays active.
Without a saved copy, only manual bans apply.
Finish active Bilibili work before switching accounts.
If file cleanup fails, select Finish recovery before using Bilibili again.
Navigation and rating drafts remain available during sign-in.
Closing the dashboard server cancels a sign-in that has not reached activation.

Select Token usage in the dashboard to inspect a saved recommendation round.
The report remains available before publication and after a pause or failure.
It shows input tokens, output tokens, cached input, and each step's share of reported tokens.
Reported tokens equal input plus output. Input totals include cached input.
Local search, screening, evidence preparation, and scoring do not use model tokens.
Quality assessment combines caption and frame input in the same model call.
Caption rows retain their IDs, timestamps, and text in compact arrays.
Assessment workers use task-specific instructions through the supported Codex `model_instructions_file` setting.
This setting applies only to child workers. It does not change the user's Codex configuration.
The saved model, reasoning effort, quality criteria, and evidence checks remain unchanged.
The report does not estimate separate image-token counts.
The report includes retries and failed attempts when Codex reports their usage.
The report marks missing measurements as unreported. It does not estimate their tokens.
Stage percentages use only reported tokens, so a report can be partial.
Historical rounds use the saved measurements that remain in their checkpoints.
Cached assessments reused from an earlier round do not create new token usage.
The report excludes the host chat and does not measure subscription quota or money.
Opening it makes no model requests.

The terminal provides the same saved report:

```powershell
uv run --no-sync broader-vids usage
uv run --no-sync broader-vids usage RUN_ID
```

The first command selects the latest round. The second selects an exact round.
Codex exposes model usage through its [JSON event stream](https://learn.chatgpt.com/docs/non-interactive-mode).

Calibration remains optional and starts empty for a new profile.
The current dashboard does not select examples automatically.


## Calibration and evidence maintenance

```powershell
uv run --no-sync broader-vids calibrate start
uv run --no-sync broader-vids calibrate list
uv run --no-sync broader-vids learn status
uv run --no-sync broader-vids learn fit
uv run --no-sync broader-vids learn evaluate
```

`calibrate start` selects thirty eligible, previously unrated videos from existing verified evidence.
It does not discover or assess replacement videos automatically.
`calibrate start --new` requests another cohort after the current reservation ends.
`learn fit` rebuilds the current model.
`learn evaluate` calculates or retrieves the applicable held-out result.
See [calibration and mathematics](SCORING.md).

Refresh one video's metadata without media downloads or model calls:

```powershell
uv run --no-sync broader-vids metadata VIDEO_URL --use-browser
```

Repair rated evidence that lacks current source information:

```powershell
uv run --no-sync broader-vids learn repair-evidence --max-seconds 180 --max-videos 1
```

This operation can refresh metadata and call the model with retained captions and frames.
It preserves ratings and original scorecards.
It starts no new media download.
Its limits are one to three videos and at most 600 seconds.

Label saved videos from retained captions:

```powershell
uv run --no-sync broader-vids learn label-videos --max-videos 60 --max-seconds 900
```

This operation generates content topics and audience levels.
It downloads no new videos.
Existing cached labels avoid repeated model calls.
New quality assessments include these descriptors in their normal response.

## Inspect discovery and evidence

```powershell
uv run --no-sync broader-vids topics
uv run --no-sync broader-vids discover youtube --seed 42
uv run --no-sync broader-vids discover bilibili --curated
uv run --no-sync broader-vids queries --platform youtube --export my-queries.json
uv run --no-sync broader-vids discover youtube --bank my-queries.json
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
| `--model` | Use the model selected by the invoking host |
| `--effort` | Use the reasoning effort selected by the invoking host |

Standalone discovery defaults to 100 requests and 600 seconds.
Its shortlist is a candidate preview, not a quality-ranked recommendation list.
If local chat metadata is unavailable, supply both `--model` and `--effort` from the host's selected settings.
These arguments also apply to `evidence prepare`.
Preparation saves the settings for later assessment and continuation.
Continuation uses saved settings rather than accepting replacements.

Prepare or assess one playable video:

```powershell
uv run --no-sync broader-vids evidence prepare VIDEO_URL --use-browser --asr-model base
uv run --no-sync broader-vids evidence assess RUN_ID --mode augmented
uv run --no-sync broader-vids evidence captions RUN_ID
uv run --no-sync broader-vids evidence frame RUN_ID --at 120 --reason 'Check the diagram'
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
New evidence jobs allow two model calls and sixteen submitted images in total.
The initial frame sample has at most four images with captions, or eight without captions.
A failed model response can use the second call within these limits.
Older jobs retain their saved limits.
New assessment jobs can reuse compatible saved captions and frames at any reasoning effort.
A newer incompatible bundle does not hide an older compatible bundle.
Bundle reuse copies evidence, not scorecards.
The new job performs its own assessment. It does not change the source bundle.
Interrupted copies resume from verified files.
The engine removes temporary copies after failure or before the next reuse attempt.
Add a frame before the augmented assessment, within that limit.

## Clean temporary evidence

```powershell
uv run --no-sync broader-vids cleanup
uv run --no-sync broader-vids cleanup --apply
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
uv run --no-sync scripts/test.py
npm test
npm run check:web
```

The default Python command runs the full suite with up to four isolated worker processes.
Each worker runs one test file at a time.
Files use separate temporary profiles, ports, and mock state.
The runner reports failures and the slowest tests.
It starts slower files first using timings in the ignored `.cache/test-durations.json` file.
Only one browser-test file runs at a time. Other workers continue core tests.
Each file keeps its own process and temporary profile.
Layout checks run by default. Inspection screenshots require `BROADER_VIDS_TEST_SCREENSHOTS=1`.
It returns a nonzero exit code when a test or worker fails.
The GitHub Actions test step uses three workers. Browser-test files remain serial.
Recommendation and calibration fixtures create seed records in one transaction.
Production requests and checkpoint tests retain their normal durability checks.
The ordinary checks workflow cancels superseded checks for the same source branch.
Its group also combines overlapping push and pull-request checks. Fork branches use separate repository names.
The release workflow keeps its separate publication controls.
Use a smaller group for a change in one area:

```powershell
uv run --no-sync scripts/test.py --group core
uv run --no-sync scripts/test.py --group dashboard
uv run --no-sync scripts/test.py --pattern test_preflight.py
```

Core excludes files whose names contain `dashboard`.
Dashboard includes browser tests and the dashboard server lifecycle tests.
The full suite includes both groups.
Use `--jobs 1` to run isolated files in sequence.
Use the standard command to debug tests in one process:

```powershell
uv run --no-sync python -m unittest discover -s tests
```

Tests use temporary data and controlled responses.
Optional media tests need their dependencies.
The development dependencies include NumPy so transcription buffer tests run without installing speech inference or downloading models.
Unit tests do not require platform sign-in or subscription model calls.
Run fixtures supply explicit model settings instead of reading your current Codex chat.
Model-inheritance tests create temporary session files with controlled settings.
Dashboard browser tests require Playwright and a supported local browser.
Local Windows browser tests use installed Edge by default.
CI installs the Playwright Chromium headless shell on both operating systems.
Set `BROADER_VIDS_TEST_BROWSER=chromium` to use that browser locally.
Linux tests use Playwright Chromium. Install its browser and system dependencies before running tests:

```bash
uv run --no-sync playwright install --with-deps chromium
```

The GitHub Actions workflows install the matching headless shell before the test step.
CI uses `scripts/test.py --jobs 3 --verbose` and limits that step to ten minutes.
The runner reports active files every fifteen seconds. Timeout reports include the last running test.
CI uses the runner scratch directory for temporary profiles.
Path tests compare resolved paths. Reset and Clean tests use the exact directory shown by each preview.
Dashboard tests check drafts, failed writes, duplicate feedback, dialogs, platform navigation, and responsive themes.
Keep screenshots and other visual checks under the ignored `.cache/` directory.
Browser tests reuse a browser process within each worker and create a fresh context for each test.

## Remote plugin installation

The [README](../README.md#install-the-plugin) gives GitHub and npm installation procedures.
The desktop app accepts the public GitHub repository directly through Add marketplace.
The repository catalog points to the released npm package.
GitHub provides the catalog, not an unbuilt engine checkout.

`npx --yes broader-vids@latest plugin install` provides a separate npm entry point.
It registers an npm-only marketplace named `broader-vids-npm` and installs `broader-vids@broader-vids-npm` through Codex.
Its source pins the version of the running npm package. The `latest` selector retrieves the current public bootstrap.
This route does not require the GitHub catalog.
The package contains the engine wheel, launcher, skills, and dashboard assets.
No npm lifecycle script registers plugins or installs the runtime.

The npm installer stores its small catalog under `CODEX_HOME/broader-vids-npm`.
When CODEX_HOME is absent, it uses `~/.codex/broader-vids-npm`.
This host metadata is separate from profile storage. Reset and Clean do not uninstall the plugin or remove its catalog.
An installation lock prevents concurrent writes. A failed command releases that lock and preserves the catalog for retry.
If an installer stops unexpectedly, check for surviving commands before removing its stale `install.lock` file.
The installer does not remove uncertain locks automatically.

Choose one marketplace route to avoid installing duplicate copies.
Remove a plugin through its original source:

```powershell
codex plugin remove broader-vids@broader-vids
codex plugin remove broader-vids@broader-vids-npm
```

Each command removes only the named installation.
The desktop app can also uninstall the corresponding plugin.
External profiles remain available after uninstall.

For terminal commands without plugin registration, `npm install -g broader-vids` installs only the launcher.
Use guided plugin setup to complete onboarding for chat workflows.
The standalone `setup` command installs the runtime but does not complete onboarding.

## Build and install locally

### Install the local Codex plugin

Run the release preparation command from the source folder:

```powershell
npm run release:prepare
codex plugin marketplace add ./dist/local-marketplace
codex plugin add broader-vids@broader-vids-local
```

The command builds the Python wheel, packs and verifies the npm archive, builds the plugin ZIP and local marketplace, and writes `dist/release.json` and `dist/SHA256SUMS`.
The generated local marketplace contains the installable plugin payload.
These commands install locally and do not publish anything.

Open a new desktop or CLI conversation after installation.
For installation through the ChatGPT desktop app, follow [the README](../README.md#install-the-plugin).
Use the installed Broader Vids setup skill for guided setup.
The host exposes the recommendation, setup, and dashboard skills through its skill menu.
Their skill names include `broader-vids`, `broader-vids-setup`, `broader-vids-dashboard`, `broader-vids-reset`, and `broader-vids-clean`.
Use the invocation syntax that the host shows.
The manifest declares the onboarding skill through `extensions.com.openai.onboardingSkill`.
Allow the host to access the selected external profile, required network services, and existing Codex session when prompted.
If permission is unavailable, setup reports the incomplete step and preserves completed work.
See the [official local plugin guide](https://developers.openai.com/plugins/build/plugins).

### Select an existing profile

From the plugin directory, inspect or save the data selection:

```powershell
node bin/broader-vids.cjs data show
node bin/broader-vids.cjs data use "C:\path\to\my profile"
node bin/broader-vids.cjs data reset
```

`data use` records a path without moving or copying profile data.
`data reset` removes only the saved selection.
It preserves every profile.
The selection lives in `%LOCALAPPDATA%\broader-vids\launcher.json` on Windows.
An explicit `--data-dir` overrides `BROADER_VIDS_DATA_DIR`, the saved selection, and the system default, in that order.
Source commands and installed commands honor the same selection.
Use an explicit temporary directory for installation tests.

### Open or stop the dashboard

```powershell
node bin/broader-vids.cjs dashboard open
node bin/broader-vids.cjs dashboard open --no-browser
node bin/broader-vids.cjs dashboard status
node bin/broader-vids.cjs dashboard stop
```

Open starts or reuses the verified server for the selected profile.
It requests the system browser and returns the exact URL.
`browser_status: launch_requested` means the operating system accepted the request. It does not check that the page rendered.
If the system cannot start the browser, the command returns `manual_open_required` and keeps the server available.
Open the returned URL in your system browser if an embedded browser stays blank.
`--no-browser` returns its URL without opening the system browser.
The dashboard skill presents that URL in chat.
A busy preferred port causes the server to select a free port.
Stop authenticates the registered server before requesting shutdown.
It does not stop a process based only on a PID or port.
Private control records and bounded logs reside in the profile's `.dashboard/` directory.
The foreground `serve` command remains available for terminal use.

### Prepare a release

The release preparation command creates and checks local release artifacts:

```powershell
npm run release:prepare
```

It builds a Python wheel, packs and verifies the npm archive, builds a plugin ZIP and generated `dist/local-marketplace`, and writes `dist/release.json` and `dist/SHA256SUMS`.
It does not upload artifacts.

The public npm package is `broader-vids`.
The GitHub marketplace catalog uses the name `broader-vids` and tracks the npm `latest` distribution tag.
Users install the marketplace plugin from the README instructions.
The npm `plugin install` command registers the full plugin. A plain global npm install provides only the launcher.

### Automatic public releases

After a push updates `main`, its successful `checks` run starts `.github/workflows/release.yml`.
Direct commits and merged pull requests both qualify.
Other branches and unmerged pull requests do not publish packages.
The workflow checks the source repository, main branch, push event, and exact tested commit before privileged work.
A newer main commit can supersede an earlier queued release.
Automatic runs reuse the successful Windows and Ubuntu checks. A manual dispatch runs those checks again.

Releases run in sequence. The next patch follows the latest stable npm version.
A higher version declared in the source acts as a minimum for the release version.
The workflow synchronizes Node, Python, plugin, lock-file, and wheel-whitelist versions in a temporary release checkout.
It records the tested source commit in `package.json` under `broaderVidsRelease.sourceCommit`.
It writes the version commit to main and creates its `vX.Y.Z` tag in one atomic push.
The commit contains the tested functional source with synchronized release metadata.
A guarded push refuses to overwrite a newer main update.
The frozen starter preset remains unchanged.

The workflow prepares the wheel, npm archive, plugin ZIP, manifest, and checksums.
It checks source inclusion, versions, private-data exclusions, and archive integrity before publication.
The npm job uses OIDC through the `npm-release` environment.
Only that job receives `id-token: write` permission.
Tag creation and GitHub release assets use separate jobs with `contents: write` permission.
The workflow publishes the verified archive under `latest`, then checks public registry integrity.
It creates a GitHub release with the archives, manifest, and checksums after npm publication succeeds.
It retains workflow artifacts for seven days.

Retries resolve a tagged version commit back to its tested merge source.
They reuse a release when its recorded source commit matches that source.
An existing tag must have the expected parent and exact release tree.
The bot version commit does not allocate a second version.
GitHub does not start another push workflow for a commit pushed with the workflow's `GITHUB_TOKEN`.
See [GitHub's trigger rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).
An existing npm version must match the archive checksum. The workflow never replaces its contents.
Existing GitHub assets must match their size and checksum.
A conflict stops publication with an error.
For recovery, rerun the failed workflow or manually dispatch Release from main.
Manual dispatch uses the current main commit and requires passing checks.
It does not require a manually created tag.

### Configure automatic publication once

Complete these account settings before the first automatic release:

1. In GitHub, open this repository's **Settings → Environments**.
2. Create an environment named `npm-release`.
3. Restrict deployment to the `main` branch.
4. Omit required reviewers and wait timers when releases must proceed automatically.
5. Allow the release workflow to push version commits to main and create `v*` tags.
6. In npm, open **broader-vids → Settings → Trusted Publisher**.
7. Select **GitHub Actions** and enter the following values.

| Field | Value |
| --- | --- |
| Organization or user | `antidemosthenes` |
| Repository | `broader-vids` |
| Workflow filename | `release.yml` |
| Environment | `npm-release` |
| Allowed action | Enable direct `npm publish` |

Staging alone does not provide automatic publication. Staged packages require a separate maintainer approval.
The first successful trusted publication must complete within two days after configuration.
Recreate an expired configuration before retrying.
The workflow uses no permanent npm token.
See the [npm trusted-publisher guide](https://docs.npmjs.com/trusted-publishers/) for account controls and validation requirements.

After these settings are complete, push the reviewed changes to main directly or merge a development pull request into main.
Check both the ordinary CI run and the following Release run.
Check the npm `latest` version and matching GitHub release before announcing the update.
The account settings and the first live OIDC publication require separate acceptance. Local fixtures cannot check them.

### Update an installed plugin

Finish active recommendations and stop the dashboard before updating.
For a GitHub installation:

```powershell
codex plugin marketplace upgrade broader-vids
codex plugin add broader-vids@broader-vids
```

For an npm installation:

```powershell
npx --yes broader-vids@latest plugin install
```

Run npm acceptance commands outside this source checkout.
Npm can treat a matching source project as an installed package and omit its executable shim.
The npm bootstrap updates its existing catalog and asks Codex to install that exact package version.
The GitHub catalog follows `latest` after refresh.
The host has no separate `codex plugin update` command.
A marketplace refresh updates catalog information. The following install action selects the current plugin package.
Use the installation method that matches the existing marketplace to avoid duplicate plugin entries.

In the desktop app, refresh the marketplace and select the current plugin release.
If an update action is unavailable, use the terminal commands for that marketplace.
Start a new conversation and run the setup skill after updating.
Setup refreshes the matching runtime and preserves preferences, ratings, history, exclusions, and sign-in.
An update does not run Reset or Clean.

### Repair the installed runtime

From an unpacked package, install its external engine:

```powershell
node bin/broader-vids.cjs setup --evidence
node bin/broader-vids.cjs doctor
```

Stop the profile's dashboard with `dashboard stop` before changing its runtime.
The launcher checks the installed engine against the bundled wheel and the requirements for installed dependency groups before normal commands.
A missing, older, or mismatched build receipt requires one `setup --evidence` refresh.
This also applies to a rebuilt package with the same version.
The refresh preserves profile data and sign-in. `dashboard stop` remains available during repair.
Normal commands do not reinstall dependencies.
Finish active recommendations and Bilibili browser operations before setup.
Concurrent setup or runtime use returns a busy result instead of changing active files.
A failed child command releases its installation lock.
If a launcher exits unexpectedly, check for surviving children before repairing its lock.
The launcher does not remove uncertain locks automatically.
The launcher provides no stale-lock repair command.

`setup` installs the engine and its locked core dependencies, including OpenCC.
Its `installed` status does not mean onboarding is complete.
Use the guided setup skill to configure interests, choose platforms, and check access.
`setup --evidence` also installs browser and media tools.
`setup --asr` includes those tools and transcription dependencies.
The receipt records `core`, `evidence`, and `asr` only when setup installed those groups, with each group's requirements-file digest.
Setup requires Node.js 20 or later and UV.
It does not use an npm postinstall script.

## Tools, dependencies, and copyright

Broader Vids uses the [MIT license](../LICENSE).
The project notice names copyright holder **antidemosthenes**, year **2026**.
Keep that notice with redistributed Broader Vids code.

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
| [opencc-python-reimplemented](https://github.com/yichen0831/opencc-python) | Local Simplified Chinese keyword conversion | Apache-2.0 |
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

The Node archive contains the Broader Vids wheel and dependency lock files.
Setup always installs the core requirements, including OpenCC. Evidence and transcription retain their separate dependency groups.
It does not bundle the optional Python distributions, browser binaries, or model weights.
Explicit setup downloads selected dependencies separately.
FFmpeg binaries and model weights can have terms distinct from their Python wrappers.
Retain the notices supplied with the exact versions that you redistribute.

`uv.lock` and `package-lock.json` record resolved dependency versions.
The three requirements files under `python/` record package hashes for core, evidence, and transcription dependencies.
Check the installed distributions' license files when preparing a release.
The Broader Vids license does not grant rights to platform videos or third-party media.

### Captionless evidence and media transfer policy

New evidence jobs can use visual-only assessment when no transcript is available.
The caller subtracts ten quality points separately from the model score and from the learned quality component.
Caption-only mode requires a transcript.
Visual-only jobs use augmented mode.
Downloadable captions differ from text burned into the picture.
Bilibili downloads preserve the platform's offered CDN order and retain its backup routes.
The engine does not assume that Akamai or Bilivideo always provides the faster transfer.
At most two Bilibili evidence preparations run concurrently.
The engine does not delay Bilibili download starts by a set interval.
Slow transfers can switch to an offered backup within the existing retry and resource limits.
Retries cycle through the offered routes, so a failed backup does not receive every remaining attempt.
The engine keeps a slow connection when its recent rate predicts completion within the remaining allowance.
Rate tracking uses bounded samples and stops when the stream finishes or no transfer decision needs it.
At the same resolution, Bilibili selects lower bitrates before codec preferences.
This avoids a larger H.264 stream when a smaller AV1 or HEVC stream is available.
The size check uses exact sizes or estimates before transfer.
The preferred low-resolution range accepts heights from 200 to 480 pixels.
This includes nominal 240p streams whose actual height is 238 pixels.
The existing 720-pixel fallback remains available.
Transfers use 2 MiB chunks and 64 KiB buffers of constant size.
YouTube and Bilibili use the same retry policy.
Video metadata and downloads allow one initial attempt and up to three retries for recoverable exceptions.
Downloads can use up to four total attempts.
Byte or time limits can stop retries earlier.
Do not claim four attempts when a saved limit stops them.
Other recoverable candidate exceptions use a persistent limit of three retries before the engine skips that video.
Continuation does not reset this retry count. Exhausted videos do not block the remaining candidates.
Manual cancellation preserves the candidate checkpoint and does not consume a retry.
Existing request, media, image, and model-call limits can stop retries earlier.
Retries use offered routes and resume partial files.
If a server rejects resume, the replacement transfer counts against the same byte allowance.
Already completed streams do not count again when a later stream requires a retry.
Permanent media errors, such as HTTP 404, make that candidate unavailable without blocking other assessments.
Interrupted fragment downloads close their owned files before a retry starts.
The final rename retries in place without downloading completed fragments again.
A temporary file-in-use error receives at most three retries within the same allowance.
If the lock remains, the engine skips that video and reports the failure after the lists.
Permission or disk-space failures stop shared work so the user can repair local storage.
A transfer with an alternative route receives a share of the remaining transfer time.
The engine does not interrupt that share when recent progress predicts completion within the candidate allowance.
A single-route transfer has no artificial time slice. Socket and cumulative time limits still apply.
Media transfer reserves up to thirty seconds for duration checks and frame extraction.
Retries share the existing time and byte allowance.
A size limit, cancellation, or access challenge stops retries early.
The engine does not retry access challenges.
A candidate that exhausts its download size, time, or CDN attempts becomes unavailable for that round.
The round can finish if enough other videos pass quality checks.
This failure does not assign a quality score or a negative preference rating.
The engine removes unusable partial media and retains diagnostics, captions, and cumulative byte charges.
Platform access failures and shared run limits still pause work.
Parallel workers wait for existing reservations before assigning a reduced byte allowance to another video.

### Failure recovery and progress

Candidate failures do not cancel unrelated inspections.
An invalid scorecard can receive a repair within the same candidate retry limit and saved model-call allowance.
The repair prompt includes the validation failure.
Exhausted per-video attempts produce an unavailable result instead of an endless retry.
Access challenges pause the affected platform.
Model authentication failures or provider limits stop shared model work.
The report preserves the first failure and separate later issues.
The final response shows all requested recommendation lists before its candidate-failure summary.
The summary lists skipped videos, recovered downloads, actual attempt counts, and brief reasons.
The structured report exposes these records as `candidate_failures`.
Retry counts respect separate transfer sequences across continuation.
The report retains recovered downloads from the current round after continuation.
Reused evidence from an earlier round does not create a new recovery report.

Preparation time starts after a preparation slot becomes available.
Its stage allowance is at most 300 seconds.
Model assessment time starts after a model slot becomes available.
Its call allowance is at most 180 seconds.
An optional parent deadline includes queue waits and all work.
Without an explicit total allowance, only individual operations have timeouts.
Continuation preserves an explicit deadline and all cumulative request, media, and model allowances.

YouTube metadata retrieval uses one active watch-page request and starts at least one second apart.
Bilibili metadata retrieval uses at most two concurrent requests and starts at least one second apart.
Workers retain server-issued session cookies in memory.
Pacing waits count toward an optional saved time allowance.
The [yt-dlp request-rate guidance](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#common-youtube-errors) recommends longer waits between downloads when rate limits occur.
YouTube can still require sign-in or verification.
Such a response pauses the platform without an automatic retry or a negative video rating.
Review the reported video in your system browser.
Continue only after you restore access.
Browser sign-in is not imported automatically. Increasing the request cap does not repair an access challenge.
They share one request counter and retain the existing access checks.
Video downloads omit audio unless transcription requires it.
Exact or estimated selected-stream sizes support a check before transfer.
Transfer checkpoints record observed bytes and bound unreported bytes after interruption.
Reports identify conservative byte charges as upper bounds.

Terminal progress appears on standard error during a run.
The final JSON result remains on standard output.
Reports include phase times, queue waits, assessment times, caption counts, and cache hits.
Assessment times include worker startup and access checks.
They do not measure isolated model inference time.
Paused rounds can show a provisional preview in the digest and dashboard.
The dashboard labels the preview and allows ratings before publication.
That preview does not publish recommendations or change pending membership.

Scorecard schemas and validators use the same topic-label length bound of 120 characters.
See the [OpenAI Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs) for supported schema constraints.
