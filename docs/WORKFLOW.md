# Recommendation workflow

Deepvids finds videos, checks their content, and learns from explicit feedback.
This document explains the user workflow.
See the [terminal reference](REFERENCE.md) for commands.

## 1. Set your interests

Choose broad subjects that you enjoy.
Examples include mathematics, physics, travel, food, games, and music.
Deepvids stores these choices in your local profile.
Search activity does not add a preference automatically.

Use the separate Bilibili browser window to sign in.
Public searches use a separate anonymous session.
Full-video evidence can require the saved sign-in.

## 2. Explore familiar and new topics

Deepvids keeps a hierarchy of topics.
For example, mathematics can contain algebra, which can contain algebraic geometry.
The hierarchy describes search coverage.
It does not prove that you like every child topic.

Each new discovery round targets twelve existing topics and five proposed topics.
Weighted random selection favors familiar topics and topics with less search history.
The model proposes new subjects or narrower branches when useful.
The engine checks search results before it activates a proposed topic.
A small initial hierarchy can produce fewer than twelve existing topics.

YouTube discovery uses keyword searches and creator video lists.
Bilibili discovery adds anonymous Home recommendations.
Queries include narrower subjects and subject intersections.
Discovery does not provide a uniform sample of either platform.

## 3. Screen candidates

Each platform collects approximately 100 fresh candidates.
The engine retrieves counts, descriptions, tags, and available duration information.
It compares community response and query relevance before detailed quality assessment.
This screening does not require a model quality judgment.

The inspection list contains up to fifty videos per platform.
Existing unwatched recommendations keep guaranteed places until you watch or dismiss them.
The remaining places balance familiar and unrelated topics when available.
The engine limits fresh places for each creator.

The exact Bilibili tag `视频翻译` excludes a video from final selection.
Other translation disclosures inform the later originality assessment.
A missing or inaccessible response does not count as a poor-quality video.

## 4. Check content quality

Deepvids checks one playable video or Bilibili part at a time.
It excludes durations above ninety minutes.
Longer videos receive a small initial benefit up to one hour.
The benefit does not increase after one hour.

The engine prefers native captions.
Optional local transcription supplies text when captions are unavailable.
The initial assessment uses captions and four selected frames.
The model assesses substance, directness, original contribution, and useful visuals.
Unsupported properties remain unknown.

The engine prefers small video streams for analysis.
It limits the short image edge to 720 pixels.
After a checked assessment, it removes the downloaded full video.
It retains the smaller evidence needed for later reuse.

## 5. Rank and review recommendations

A complete run selects ten YouTube videos and ten Bilibili videos.
The ranking combines quality, interest, community response, recency, and creator diversity.
Feedback can change the learned ranking.
The [scoring document](SCORING.md) explains the formulas and limits.

Each final list includes at least two unrelated videos when enough eligible candidates exist.
Unwatched recommendations can appear again.
At fifty pending videos on one platform, new admissions wait for free places.

Read the linked digest or open the local dashboard.
Check any access failures and incomplete assessments.
The default batch stops within its thirty-minute allowance.
It can retain unfinished work instead of producing twenty complete recommendations.

## 6. Give separate feedback

| Control | Meaning |
| --- | --- |
| Specific topic | Your interest in the named narrow subject |
| Broad subject | Your interest in the named larger domain |
| Depth fit | Whether you want deeper, similar, or lighter treatment |
| Video quality | Your assessment of this video's quality |
| Watched | You watched this video |
| Dismiss | Remove this video from future recommendation lists |

Topic, subject, and quality ratings accept High, Medium, and Low.
Medium is a judgment.
A blank field means no judgment.
Check the topic label before you save feedback.

Use **Too elementary** when you prefer the subject but need more depth.
Use a specific-topic dislike when you dislike that topic within a broader subject you enjoy.
These judgments do not create a broader-subject dislike.
Depth feedback does not reduce the video's quality rating.

Opening a link does not mark it watched.
Leaving a recommendation unwatched does not create a negative rating.
You can edit or clear each feedback dimension independently.

## 7. Check calibration

Calibration compares predictions with your separate interest and quality judgments.
The workflow reserves thirty previously unrated videos across both platforms.
Some reserved videos test predictions before their labels enter training.
This separation limits feedback leakage into the test result.

Rate only the dimensions that you can judge.
The workflow accepts missing judgments.
It does not require a false rating to fill a quota.
New response types require their own test coverage.

## 8. Continue or request another round

Continue an incomplete batch with its printed batch ID.
Continuation retains the original topic plan and remaining allowances.
Request another recommendation run when you want a new round with a new allowance.
Both actions use the same saved profile and pending queue.

Automatic daily triggers remain planned.
The intended daily workflow runs locally through a desktop scheduled task.
It will preserve pending videos and feedback between dates.
The local computer and desktop app must run for that schedule to execute.
