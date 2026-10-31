# skillharness: v1 plan

Status: agreed direction, 2026-10-29. Nothing extracted yet.

## What it is

A runtime for Agent Skills outside Claude Code. An Agent Skill is a folder with a `SKILL.md`
(the open standard at agentskills.io: YAML frontmatter with `name` and `description`, Markdown
instructions, optional `scripts/`, `references/` and `assets/`). Claude Code runs such a folder with a
shell and a human in the loop. skillharness runs it as a service: any model, no shell, no human, the
skill's commands in a sandbox, a hard budget, a typed event stream, and every run recorded as a folder
of files with its cost.

The one-line pitch: point it at a skill folder, give it a prompt, get back the artifact the skill
makes, with the whole run on disk.

It comes out of the product-unfold backend, where this loop already builds interactive teaching
figures on Modal with Claude and GPT models. product-unfold stays private and becomes the first
consumer. The unfold skill stays private too; the public repository ships a small example skill.

## What it is not

- Not another general agent framework. Pydantic AI is the agent framework underneath and stays
  visible; skillharness is the layer that turns a skill folder into a bounded, recorded run.
- Not a chat loop. A run has one prompt in and one artifact out. No turns, no user mid-run.
- Not a skill marketplace or loader of many skills at once. One skill per run.

## The idea that makes "any skill" precise

The standard says what a skill folder contains but not how its scripts are run. In Claude Code the
model runs them with bash. Without a shell, something has to say which commands exist, what they take
and what comes back. That is the **manifest**: a small YAML file that declares, for one skill,

- the **artifact** the model writes (one file, replaced whole on every write),
- the **readable** files the model may open on demand,
- the **commands** the model may run, each with its options, what it produces, and which outputs
  come back as images for the model to look at,
- the **profiles**: named budgets (how many command runs, seconds, dollars, which options are allowed,
  reasoning effort),
- the **prompt** additions: a short note mapping the skill's shell workflow onto the tools, and
  per-run lines filled from variables,
- what the skill **needs in its sandbox** (Node, apt packages, extra folders), so the Modal image can
  be built from it.

The manifest lives in the skill folder as `harness.yaml` when the skill ships with one, or beside the
consumer's code when the skill is read-only (unfold's case: product-unfold keeps `unfold.harness.yaml`
and passes it in). Everything the current four unfold tools hard-code becomes manifest data.

### Manifest, v1 shape

```yaml
artifact:
  name: figure.js
  must_contain: "figure("          # optional cheap check before accepting a write
readable:                          # globs relative to the skill folder
  - references/*.md
  - examples/*.js
commands:
  build:
    description: >
      Check, bundle and screenshot the figure you last wrote, like `node scripts/build.mjs figure.js`.
      The check log and every check-sheet image come back in the result; look at every image.
    run: node {skill}/scripts/build.mjs {artifact}
    options:
      state:     {type: string, argv: "--state {value}", description: "control values for an extra sheet, e.g. seat=2,air=true"}
      zoom:      {type: flag,   argv: "--zoom",      description: "also the four quarters enlarged"}
      phone:     {type: flag,   argv: "--phone",     description: "also a 390 px wide sheet"}
      dark_shot: {type: flag,   argv: "--dark-shot", description: "also a dark-theme sheet"}
    outputs:
      page: figure.html              # named files read back from the workdir when the command succeeds
    images: ["figure.png", "figure-*.png"]   # returned to the model as images, in the order listed
    timeout: 180
    counts_toward_budget: true
    required_before_finish: true     # finish is refused until this command has succeeded once
finish:
  summary: One or two lines on what the artifact shows.
  extra:
    skipped: The steps of the skill's list you did not reach or that did not apply, with why.
profiles:
  quick:    {runs: 2,  seconds: 120,  dollars: 1.0,  options: [],                            effort: medium}
  standard: {runs: 5,  seconds: 300,  dollars: 2.0,  options: [state],                       effort: high}
  deep:     {runs: 8,  seconds: 600,  dollars: 4.0,  options: [state, phone, dark_shot],     effort: high}
  advanced: {runs: 10, seconds: 1200, dollars: 10.0, options: [state, phone, dark_shot, zoom], effort: xhigh}
prompt:
  note: |                            # appended after SKILL.md; {tools} and {budget} are filled in
    You are running inside a service, not a terminal ...
  run: |                             # per-run lines, filled from the request's variables
    Level: {level_words} (`level: "{level}"`).
    Depth: {profile} ({profile_minutes}; `depth: "{profile}"`).
    Both were chosen by the user from a menu. Do not change them, and do not ask.
sandbox:
  node: "20"
  apt: [chromium, fonts-noto-color-emoji]
  setup: ["printf '...' > /usr/local/bin/chromium-headless && chmod 755 /usr/local/bin/chromium-headless"]
  env: {CHROME_PATH: /usr/local/bin/chromium-headless}
  cpu: 2
  memory_mib: 2048
```

A skill with no commands is still valid: the model reads, writes the artifact and finishes. That is
the degenerate case and the first test.

## Tools derived from the manifest

Exactly these, generated at run start:

- `read_file(path)`: one readable file. Unknown path: a retry message listing what is readable.
- `write_file(source, plan=None)`: replace the artifact. The first `plan` becomes the `plan` event.
- one tool per command, named after it, with one parameter per option. Options the profile does
  not allow are ignored and the log says so. Over budget: refused once with a message, ended on the
  second attempt.
- `finish(summary, **extra)`: ends the run; refused until every `required_before_finish` command has
  succeeded once.

No shell, no network from the model's side, ever. This is the security story and it is simple.

## The contract

Unchanged in spirit from product-unfold, renamed to be skill-neutral.

- `RunRequest`: `run_id`, `prompt`, `skill` (name and git hash), `profile`, `model`, `vars`
  (free-form, filled into the prompt template; level and depth for unfold).
- `Budget`: runs, seconds, dollars, allowed options, effort, request limit. Enforced by the harness.
- Events: `plan`, `artifact_written`, `command_started`, `command_finished`, `note`, `finished`,
  `failed`. Pydantic models, one discriminated union, JSON on the wire.
- `finished` carries the artifact keys, the last successful outputs, rounds, seconds, `UsageTotals`
  (tokens by kind and model cost), `stopped_by` (model, run cap, time, cost) and optional
  `InfraTotals` (sandbox and function seconds, store operations, infra cost).
- `Runner`: `run(request) -> AsyncIterator[Event]`. One implementation in v1 (Pydantic AI). A remote
  runner that forwards another runner's events (the Modal one) is a consumer concern in v1.

## The run folder

Every run leaves `runs/<run_id>/` in the active store. It is the whole record.

| File | What |
|---|---|
| `request.json` | the request, the skill hash, the budget, the time |
| `<artifact>` | the latest artifact |
| `rounds/NN/<artifact>` | the artifact each command run used |
| `rounds/NN/<outputs>`, `rounds/NN/*.png` | that round's outputs and images |
| `rounds/NN/<command>.txt` | a command log too long to stay in the history, in full |
| `writes/NN/<artifact>` | every accepted version of the artifact, what a version stub points at |
| `<outputs>` | the last successful command's outputs, copied to the top at finish |
| `transcript.json` | every message and tool call as the model last saw it: images stripped, older items as stubs |
| `result.json` | the `finished` or `failed` event |
| `infra.json` | infrastructure seconds and cost, when the consumer measures them |

## Package layout

```
skillharness/
  contract.py     RunRequest, Budget, events, UsageTotals, InfraTotals, Runner protocol
  skill.py        Skill: SKILL.md frontmatter, readable files, git hash, export at a commit
  manifest.py     Manifest models and loader
  models.py       ModelSpec table with prices; consumers may pass their own table
  prompt.py       system prompt: SKILL.md + manifest note + run lines
  tools.py        the tool factory
  runner.py       the Pydantic AI loop: budgets, usage, transcript, result
  sandbox/        CommandBackend protocol; local.py (subprocess); modal.py (one sandbox per run,
                  network blocked, no secrets, image built from the manifest's sandbox section)
  store/          FileStore protocol; local.py; s3.py (R2 and any S3-compatible store, via obstore,
                  with operation counts for the cost line)
  context/        v1.1: elision and tool-result offloading policies
  evals.py        run a list of prompts, measure time, tokens, cost per run, write a Markdown table
  cli.py          skillharness run <skill> "<prompt>" --profile quick --model claude-opus-5-5
examples/
  <example skill>/  SKILL.md, harness.yaml, scripts/
tests/
docs/
```

Dependencies: `pydantic-ai-slim[anthropic,openai]`, `pyyaml` (frontmatter and manifest), `obstore`
(store), `modal` as an optional extra. All but pyyaml are already on this machine through
product-unfold, so uv hardlinks them. pyyaml is under 1 MB.

## What moves from product-unfold, and what stays

| product-unfold today | skillharness | stays in product-unfold |
|---|---|---|
| `runner/__init__.py` (contract) | `contract.py`, renamed fields | |
| `skills.py` (loader, git hash, export) | `skill.py` | the registry entry for unfold, its manifest |
| `prompt.py` | `prompt.py`, templated | the unfold note and run lines, now manifest text |
| `runner/pydantic_ai_runner.py` | `tools.py`, `runner.py` | |
| `build/` | `sandbox/` | |
| `store/` | `store/` | |
| `config.py` models and prices | `models.py` | depths and levels, their mapping to profile and vars |
| `scripts/run_evals.py` | `evals.py` | the unfold eval prompts and the measurements document |
| `cli.py` | `cli.py` | |
| `modal_app.py`, `deploy.py` | the sandbox image builder and the Modal sandbox | the app, the `run` function, the secrets, the deploy |
| `service.py`, `api.py`, `web/` | | all of it: jobs, SSE, endpoints, the page |
| `runner/modal_runner.py` | | stays: it is the service's hop to its own Modal function |

The test suite moves with the code. The scripted-model tests that use unfold's example figure are
rewritten against the example skill; product-unfold keeps its own end-to-end test.

## Migration: strangler, not rewrite

product-unfold keeps working at every step, and the deployed service runs the current code until the
last step.

1. **Skeleton and the degenerate skill.** Package, contract, skill loader, manifest, prompt, tools,
   runner, local sandbox, local store, CLI. The example skill with no commands runs end to end with a
   scripted model and with a real model. Tests pass.
2. **Commands and images.** The example skill grows a command that produces an image; the tool
   factory, the round folders and the image return path are proven on it.
3. **Modal sandbox and S3 store** move over with their tests (fake sandbox, in-memory bucket).
4. **product-unfold switches.** `unfold.harness.yaml` written from the current hard-coded tools and
   budgets; backend code deleted module by module as the library replaces it; the editable path
   dependency used locally. All product tests pass; one quick figure through the local path.
5. **Public twin and first tag.** README, example, licence. `git push public main`, tag `v0.1.0`.
   product-unfold's `pyproject.toml` points at the tag; Modal and Render images rebuilt; one quick
   figure through the deployed path, about $0.20 model and $0.01 Modal.
6. **product-unfold's documents updated**: architecture page, README, CLAUDE.md.

Sessions: 1 for steps 1 and 2, 1 for steps 3 and 4, 1 for steps 5 and 6.

## After v1: context management (the part worth sharing)

This is where the repository becomes interesting rather than one more loop. Each item is measured
with the eval harness, tokens and cost per round with and without the policy, and the measurements
are published alongside the code.

1. **Image elision.** A check-sheet image is worth looking at once. After it has been superseded by a
   newer round, its bytes leave the message history and a one-line text takes their place, naming the
   store key. Pydantic AI's history processors are the hook.
2. **Tool results in the store.** Every tool result is written under `rounds/NN/` as it is produced.
   Large results reach the model as a head plus a handle; a `read_result(key, part)` tool fetches
   more on demand. The transcript becomes the index, not the payload.
3. **Transcript compaction.** Past a token threshold, older rounds are summarised into a short note
   (what was tried, what the check said) and the raw messages drop out. Measured against the quality
   bar, since this is where quality can quietly fall.
4. **Caching-aware prompt layout.** SKILL.md and the manifest note at the top, stable across rounds,
   so provider prompt caching does the heavy lifting. Measured by cache-read share.

## Example skill

Small, MIT-safe, with one command that returns an image, so the build-and-look loop is visible in the
public repository without the unfold skill. Candidates, in order of preference:

1. **A chart skill**: the model writes a short Python plotting file, `render` runs it in the sandbox
   and returns the PNG, the skill's instructions say what a good chart looks like. Needs matplotlib in
   the sandbox; already cached on this machine.
2. **An SVG diagram skill**: the model writes an SVG, a check script validates it and rasterises it.
   Needs a rasteriser; Chrome locally, Chromium on Modal, which is heavier than it looks.

Decision: the chart skill unless a size check says otherwise.

## Repositories and releases

- This folder is the private working repository. `dev` for daily work, `main` for the clean story.
- The public twin, `github.com/crossvalidator/skillharness`, is created at step 5 and receives `main`
  by a plain push every few days. Version tags live there; product-unfold pins them. Render and Modal
  image builds fetch from the public tag with no credentials.
- Commits carry the author's name only. MIT licence. No secrets, no account ids, no Modal profile
  names in the code.
- PyPI later, when the manifest format has stopped moving.

## Decisions taken here, for the record

- Name: skillharness (free on PyPI, GitHub and npm as of 2026-10-29).
- Manifest in YAML next to SKILL.md, or supplied by the consumer for read-only skills.
- One artifact per skill in v1. Several artifacts are a manifest extension, not a redesign.
- Images come back inside the command's tool result, as today; elision is a later policy on top.
- The Modal `run` function template stays in the consumer in v1. If a second consumer appears, it
  moves into the library.
- Profiles live in the manifest; a consumer may override a profile's numbers per run.
