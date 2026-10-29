# skillharness

Run an [Agent Skill](https://agentskills.io) folder as a bounded, recorded run on any model.

An Agent Skill is a folder with a `SKILL.md`: instructions for a model, plus optional scripts,
references and assets. Claude Code runs one with a shell and a human watching. skillharness runs one
as a service would have to: the model gets exactly the tools a small manifest declares and no shell;
the skill's own scripts run in a sandbox with no network; a hard budget of runs, seconds and dollars
stops the loop; every file the run produced is kept, next to a transcript and what it cost.

```
skillharness run examples/chart "How did the share of electricity from solar and wind grow in the EU, 2015-2024?" \
    --profile quick --var audience="a newspaper reader"
```

```
run YybbgZum0qv8  skill chart@fe2096a5da9e  model claude-opus-5-5  profile quick
[   7.9s] plan {"text": "A line chart for a newspaper reader: the combined solar and wind share ..."}
[   7.9s] artifact_written {"key": "chart.py", ...}
[   7.9s] command_started {"command": "render", "argv": []}
[   9.6s] command_finished {"command": "render", "ok": true, "images": ["rounds/01/chart.png"], ...}
[  17.2s] artifact_written {"key": "chart.py", ...}
[  17.2s] command_started {"command": "render", "argv": []}
[  18.9s] command_finished {"command": "render", "ok": true, "images": ["rounds/02/chart.png"], ...}
[  20.9s] finished (model) in 21 s: 2 rounds, 3 requests, 11864 in (6256 cached) / 2068 out, $0.071
    I made a line chart of how much of the EU's electricity came from solar and wind each year ...
    caveats: ["The request didn't include any numbers, so the yearly figures are my own rounded estimates ...", ...]
files: runs/YybbgZum0qv8
```

The model wrote a chart, looked at the rendered image, noticed the title was cut off, fixed it and
handed over. That look-check-fix loop is the whole point, and the harness makes it available to any
skill that can describe its artifact and its check command.

## Why

A skill written for Claude Code assumes a shell, a file system and someone reading along. Behind an
HTTP endpoint none of that holds: the model must not run arbitrary commands, the run has to end on
its own, the output has to land somewhere a web page can serve it, and someone is paying per token.
skillharness is the layer between a skill folder and a service. It is provider neutral (Pydantic AI
underneath; Anthropic and OpenAI models out of the box), it needs no database, and it records every
run the same way, so runs can be compared, replayed and billed.

## Install

Python 3.12 or newer. From a tag:

```
uv add "skillharness @ git+https://github.com/crossvalidator/skillharness@v0.1.0"
```

or, to work on it:

```
git clone https://github.com/crossvalidator/skillharness
cd skillharness
uv sync            # library, CLI and the test dependencies (matplotlib for the example skill)
uv run pytest
```

Provider keys come from the environment or a git-ignored `.env` in the working directory:
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`. The optional `modal` extra adds the Modal sandbox backend.

## How a skill becomes a tool set

The skill folder stays as it is. Next to its `SKILL.md`, or supplied separately for a skill you do not
own, a `harness.yaml` manifest says what the model is allowed to do. The example chart skill's:

```yaml
artifact:
  name: chart.py                      # the one file the model writes
  must_contain: "def draw("
readable:                             # what read_file may open, relative to the skill folder
  - references/*.md
  - examples/*.py
commands:
  render:                             # becomes a tool named render
    run: "{python} {skill}/scripts/render.py {artifact}"
    options:
      wide: {type: flag, argv: "--wide", description: "16:9 canvas for a slide"}
      dark: {type: flag, argv: "--dark", description: "dark background"}
    outputs: {png: chart.png, svg: chart.svg}
    images: [chart.png]               # returned to the model as images, in this order
    timeout: 60
    required_before_finish: true
finish:
  summary: One or two lines on what the chart shows.
  extra:
    caveats: {type: list, description: "Numbers you assumed, limits of the view."}
profiles:
  quick:    {runs: 2, seconds: 120, dollars: 0.5, options: [], effort: medium, minutes: about 1 minute}
  standard: {runs: 4, seconds: 300, dollars: 1.5, options: [wide, dark], effort: high, minutes: about 3 minutes}
prompt:
  run: |
    Audience: {audience}.
    Profile: {profile} ({profile_minutes}).
sandbox:
  pip: [matplotlib]
```

From this the harness generates the model's tools: `read_file` over the readable globs,
`write_file` for the artifact, one tool per command with the declared options as typed parameters,
and `finish` with the declared fields. Nothing else exists; there is no shell tool. The system prompt
is the `SKILL.md` body, a note describing the tools and the budget, and the manifest's run lines with
the caller's variables filled in. `skillharness show examples/chart --profile quick` prints it.

Profiles are budgets. A profile caps the number of command runs, the wall-clock seconds and the
dollars, chooses the reasoning effort, and says which command options are available at that depth;
options outside the profile are accepted and ignored, with a note in the command log, so the same
skill text works at every depth. The harness refuses a finish before a required command has
succeeded and stops the run when a cap is hit, reporting which one.

## What a run leaves behind

Every run gets a folder in the file store (local disk, or any S3-compatible bucket such as
Cloudflare R2):

```
runs/<run id>/
  request.json          prompt, profile, model, variables, budget, skill name and git hash
  chart.py              the artifact, last version
  chart.png  chart.svg  the outputs of the last successful command
  rounds/01/            artifact, outputs and images of each round
  rounds/02/
  transcript.json       every message, image bytes replaced by their store key
  result.json           the finished or failed event: summary, extra fields, rounds, seconds,
                        tokens, cost, what stopped the run
```

The events a consumer streams are typed and skill neutral: `plan`, `artifact_written`,
`command_started`, `command_finished`, `note`, `finished`, `failed`. A web service can forward them
to a browser unchanged.

## Using it from code

```python
from skillharness.contract import RunRequest
from skillharness.runner import PydanticAIRunner
from skillharness.sandbox.local import LocalSubprocessBackend
from skillharness.skill import load_skill
from skillharness.store.local import LocalDiskFileStore

skill = load_skill("examples/chart")                 # reads SKILL.md and harness.yaml
store = LocalDiskFileStore("runs")
runner = PydanticAIRunner(skill, LocalSubprocessBackend(skill.folder), store)
request = RunRequest(run_id="r1", prompt="...", skill=skill.ref, profile="quick", model="claude-opus-5-5",
                     vars={"audience": "students"}, budget=skill.manifest.budget("quick"), workdir="runs/_work")

async for event in runner.run(request):
    ...
```

Swap `LocalSubprocessBackend` for `skillharness.sandbox.modal.ModalSandboxBackend` and the commands
run in a Modal sandbox with the network blocked and no secrets, in an image built from the
manifest's `sandbox` section with the skill folder baked in read-only at its git hash. Swap the store
for `skillharness.store.s3.S3FileStore` and the run folders land in a bucket.

## Models

| id | provider |
|---|---|
| `claude-opus-5-5` (default) | Anthropic |
| `claude-sonnet-5-5` | Anthropic |
| `gpt-6.1-sol` | OpenAI |
| `gpt-6-astra` | OpenAI |

Prices per million tokens are in `skillharness/models.py`; the cost in every `finished` event comes
from them and the provider's reported usage, including cached input.

## Roadmap

The next work is context management, each item measured in tokens and dollars per round against the
plain loop and published with the code:

1. Image elision: a check image is worth looking at once; after a newer round supersedes it, its
   bytes leave the message history and a one-line reference to its store key takes their place.
2. Tool results in the store: large results reach the model as a head plus a handle, with a tool to
   fetch more on demand; the transcript becomes an index rather than the payload.
3. Transcript compaction past a token threshold, measured against the quality bar.
4. Cache-aware prompt layout, measured by cache-read share.

`docs/plan-v1.md` is the design and the decisions behind it. `CHANGELOG.md` lists releases.

## Status

v0.1.0. The manifest format may still change between minor versions; the event contract is meant
to stay. Extracted from a production service that builds interactive teaching figures, which is its
first consumer. MIT licence.
