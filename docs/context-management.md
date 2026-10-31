# Context management: proposal

Status: agreed 2026-10-30 (the open questions at the end record the decisions). Steps 1 and 2 of
the order of work (elision, `fetch_result`, long text results) are implemented in `context.py` and
`tools.py`; compaction is next. Follows the shape described in
"Building AI research agents" (omkard.com): elision runs continuously and loses nothing; compaction
waits behind a threshold, is structured, and defers when the timing is bad. This document says what
those two things mean for a skill run, where the differences from a research agent are, and how each
step is measured.

## What a run's context is made of

A research agent's history is mostly text results from search tools. A skill run's history is
different, and the design has to start from that. One round of the unfold skill at deep depth,
measured from the recorded quick runs and the skill's image sizes:

| Item | Where it sits | Size per round | Lives in the store already? |
|---|---|---|---|
| Check images (check sheet, state, phone, dark; advanced adds up to 4 zooms) | tool result of `build` | about 1.5k tokens each, 4 to 8 per round | yes, `rounds/NN/*.png` |
| The artifact source the model wrote | arguments of its own `write_file` call | 1k to 2k tokens | yes, `rounds/NN/figure.js` and top-level |
| Build log | tool result of `build` | under 300 tokens | no (it is in the event) |
| Thinking and prose | model responses | 0.3k to 1k tokens | n/a |
| SKILL.md, the harness note, the tool definitions | instructions, stable for the whole run | about 10k tokens | n/a |

Two consequences. First, the article's "2k tokens" threshold for text results barely applies here:
what grows the context is images and superseded artifact versions, both of which are the model's
working material from a round that is over. Second, the store already holds every one of those
files, keyed by run and round, so "write tool results to object storage" is mostly done; what is
missing is the stub in the history and a way to get a file back.

Rough scale for a deep run of 8 rounds with no management: each request re-sends everything before
it, so the images alone add up to 6k x (1 + 2 + ... + 8), about 220k input tokens, before the source
versions and the prose. Provider caching makes re-sent prefixes much cheaper than fresh tokens, but
only for a prefix that has not changed since the last request, which is the constraint that shapes
everything below.

## Principles

1. **Elision is lossless.** Nothing leaves the run folder; only the history the model sees changes.
   Every elided item is one `fetch_result` call away.
2. **Edit only the recent window.** An edit to an old message invalidates the provider's cache for
   everything after it. Elision happens at a fixed lag (a round's heavy parts are stubbed exactly
   when they age out), so the prefix before the lag point is identical from one request to the next.
3. **Compaction is rare, structured and at a boundary.** It is a separate model call and it loses
   detail; it fires at a threshold, never mid-sequence, and what it produces has a schema.
4. **Skill-neutral.** The rules live in the manifest under `context:` with defaults; the library
   knows about "images", "artifact versions", "text results" and "rounds", never about figures.
5. **Measured.** Each step is a separate change, run on the same eval prompt as the plain loop,
   with input tokens, cache-read share, cost, rounds and the output compared. Numbers go in
   `docs/measurements.md` and the README.

## Store layout

No new bucket and no new top-level folder. The run folder is the tool-result store:

```
runs/<id>/
  rounds/NN/<artifact>              the source that round built (exists)
  rounds/NN/<outputs>, <images>     what the command produced (exists)
  rounds/NN/<tool>.txt              NEW: a text result over the size limit, in full
  context/compaction-NN.json        NEW: each compaction: tokens before and after, rounds folded, the summary
  transcript.json                   what the model saw: stubs in place, with their keys (exists; gains the stubs)
```

**Handles are store keys.** `rounds/03/figure.png` is already unique within the run, readable by a
human, and the same string in the stub, the transcript, the event stream and the web page. A separate
`r_7f2a` id table would add a mapping for nothing. (A skill with ids in its outputs can still put
them in the stub text.)

## The fetch tool

One reserved tool joins `read_file`, `write_file` and `finish`:

```
fetch_result(key: str, part: int = 0)
```

Returns the stored file for a key inside this run's folder: an image as an image, text as a page of
about 2k tokens (`part` selects the page). Only keys the model has seen in a stub are valid, so it
cannot wander through the folder. A fetch counts as a request, not as a run.

## The flow

The library knows about messages, tool calls, tool results, images, text, store keys and the
manifest. It knows nothing about any skill. A **turn** is one model response plus the tool results
it caused; the run folder keeps its existing `rounds/NN/` naming for the same thing. One hook (Pydantic AI `ProcessHistory`) runs before every model request, with the history,
the previous request's usage and the manifest's `context:` section as input, and the history the
model will see as output. Four steps, always in this order.

### 1. Measure

`used` = the input tokens the provider reported for the previous request, fresh plus cached.
`budget` = the model's context window (`Model.context_window`: 1,000,000 for Claude Opus 5.5 and
Sonnet 5.5, 1,050,000 for GPT-6.1 Sol and GPT-6 Astra), or `context.budget_tokens` when set. No
tokenizer in the library.

### 2. Elide (every request, lossless)

| Rule | Default |
|---|---|
| In a tool result older than `keep_turns` turns, each image becomes a stub. | `keep_turns: 1` |
| In any tool result, text over `text_limit_tokens` is written to the store as `rounds/NN/<tool>.txt` and shown as its first 40 lines plus a stub. | `text_limit_tokens: 2000` |
| In the arguments of a tool the manifest marks `artifact: true`, every version but the latest `keep_versions` becomes a stub. | `keep_versions: 1` |
| Never touched: the current turn, the first user message, the latest result of each tool, a pending output-validator retry. | |
| An edit only ever hits the turn that just aged out, never an older message, so the cached prefix stays identical between requests. | |

A stub is one line: the store key, the tool name, the size, the status line of the tool result it came
from, and the fetch call:

```
[rounds/03/figure.png | build: passed | 1158x2190 | fetch_result('rounds/03/figure.png')]
[write_file figure.js, version 3 of 5, 2,310 chars | fetch_result('rounds/03/figure.js')]
```

Cache points go on instructions and tool definitions and after the last message elision will not
touch again. The last one is not optional: measured on the seasons eval (2026-10-30), elision with
only the provider's automatic breakpoint stalled the cache read at 13k tokens from the third request
on, because the automatic breakpoint sits at the end of the previous request, inside the turn that
is rewritten next, and a version stub rewrites a `write_file` call deep in the prefix. One explicit
`CachePoint` at the end of the last stable request restores a prefix that grows turn by turn; moving
the marker changes no content, so it never invalidates anything itself.

### 3. Decide

Skip compaction unless `used >= compact_at * budget` (`compact_at: 0.75`). Defer one turn when the
last message is a tool call whose result is not in yet, or when the last tool called is the first of a
pair listed under `context.sequences` (the article's "never mid-sequence").

### 4. Compact

- **Partition** the history into pinned, tail and middle. *Pinned*, by role: the first user message
  always; the latest call of every tool marked `pin_latest: true`; any argument field listed under
  `pin`. *Tail*: the most recent whole turns that fit in `tail_share` of budget (`0.20`). *Middle*:
  everything else, earlier summaries included.
- **Summarise** the middle with one call to the compactor model (`compactor_model`, default the
  Sonnet-class model of the run's provider: `claude-sonnet-5-5`, `gpt-6.1-sol`), output typed as the
  base schema plus the manifest's `summary_extra` fields:

  ```
  established:    [{claim, handle}]        handle = a store key that shows it
  identifiers:    [str]                    exact names, numbers, keys that must not drift
  dead_ends:      [{approach, why_failed}]
  open_questions: [str]
  current_state:  str
  next_action:    str
  ```

- **Rebuild** the history as pinned, then one user-role message headed "Earlier in this run
  (compacted)" holding the summary, then the tail.
- **Record** `context/compaction-NN.json` (tokens before and after, turns folded, seconds, cost, the
  summary) and emit `compaction_started` and `compaction_finished`. The call's cost counts toward the
  run.

### Manifest

```yaml
context:
  budget_tokens: null        # null = the model's context window; a number overrides it (tests)
  compact_at: 0.75
  tail_share: 0.20
  keep_turns: 1
  keep_versions: 1
  text_limit_tokens: 2000
  compactor_model: null      # null = Sonnet-class model of the run's provider
  sequences: []              # pairs of tool names that must not be split by a compaction
  pin: []                    # tool.field names kept verbatim, e.g. write_file.plan
  summary_extra: {}          # extra summary fields, same shape as finish.extra
tools:
  write_file:
    artifact: true           # old versions of its arguments are elided
    pin_latest: true         # its latest call is pinned through compaction
```

Every key has a default; a manifest without a `context:` section gets the behaviour above.

### What unfold adds

```yaml
context:
  sequences: [[write_file, build]]
  pin: [write_file.plan]
profiles:
  advanced:
    context: {keep_turns: 2}
tools:
  write_file: {artifact: true, pin_latest: true}
```

With elision a deep run peaks near 35k tokens and an advanced run near 50k, so at a 1M window
compaction does not fire for this skill; it is the safety net for long or text-heavy skills. Cost
for unfold is governed by elision and caching.

## What the user sees

- **Elision: nothing.** It is silent in the stream. `result.json` gains a `context` block:
  elided images, elided artifact versions, stored text results, compactions, and the request-level
  token series so the saving can be read off.
- **Compaction: two events**, `compaction_started` (round, tokens before) and `compaction_finished`
  (tokens before and after, turns folded, seconds, cost). The web page shows "Compacting the
  conversation before continuing" with the loading state for that interval, the article's wording;
  the Notes menu lists compactions with the cost line. Nothing to click, nothing to approve.
- **Transcript viewer later:** stubs render as links to the stored file, so the full history is one
  click away without the tokens.

## Order of work, each step measured on the same deep eval

0. **Baseline.** One deep run of the seasons eval on the plain loop through Modal (about $2). Keep
   the transcript, the per-request token series and the figure.
1. **Image and artifact elision + the fetch tool + cache points.** One change, because the cache
   points only pay off once the prefix is stable. Expected: input tokens down by more than half at
   deep, cache-read share up, the figure unchanged. Also the point at which the stub wording is
   tuned, since a stub the model ignores or over-fetches shows up in the request count.
2. **Text results in the store.** Small, exercised by the chart example with a deliberately noisy
   render script, since unfold does not trigger it.
3. **Compaction.** Built and exercised against a lowered `budget_tokens` override (so a deep run
   crosses it) to check the partition, the deferral, the summary quality and the rebuilt history; at
   the real budget it is not expected to fire on unfold.
4. **Write-up.** `docs/measurements.md` with the four runs side by side, README section, release
   v0.2.0, product bump, then the remaining evals at the new cost.

## Open questions

1. Budget: the model's context window with a 75% trigger (decided 2026-10-30). Only the test
   override remains to be chosen; 40k makes the seasons run cross it.
2. `fetch_result` returns images as well as text (decided 2026-10-30).
3. Compactor: Sonnet-class model of the run's provider (decided 2026-10-30).
4. End users see "Compacting the conversation before continuing" while it runs, and a line in
   Notes after (decided 2026-10-30).
5. `keep_turns` is 1 by default and 2 in unfold's advanced profile (decided 2026-10-30).
