# Changelog

## Unreleased

- Context management, step 1 and 2 of docs/context-management.md: before every model request, images
  in command results older than the newest `keep_turns` become one-line stubs, earlier artifact
  versions inside `write_file` calls become stubs, and a text result over `text_limit_tokens` is
  stored in full and shown as its first 40 lines plus a stub. Nothing is lost: the new reserved tool
  `fetch_result(key, part)` brings any stubbed item back. Only the turn that just aged out is
  rewritten, and one explicit cache point sits at the end of the last request elision will not touch
  again, so the provider reads the whole stable prefix from its cache on every request.
- Manifest: `context:` section (`budget_tokens`, `compact_at`, `tail_share`, `keep_turns`,
  `keep_versions`, `text_limit_tokens`, `compactor_model`, `sequences`, `pin`, `summary_extra`), with
  per-profile overrides under `profiles.<name>.context`. The compaction keys are parsed and validated
  now and used by the next step.
- Run folder: `writes/NN/<artifact>` for every accepted version; `rounds/NN/<command>.txt` for a long
  log. The system prompt gains a "Your history" paragraph explaining the stubs.
- `finished` and `failed` events carry a `context` block: elided images and versions, stored text
  results, fetches, compactions.

## v0.1.0 (2026-10-29)

First release, extracted from a production service.

- Manifest (`harness.yaml`) that turns a skill folder into a fixed tool set: `read_file`, `write_file`,
  one tool per declared command with typed options, `finish` with declared fields.
- Budget profiles: runs, seconds, dollars, reasoning effort, per-profile command options.
- Pydantic AI runner with a typed, skill-neutral event stream and a recorded run folder
  (artifact, per-round outputs and images, transcript with image bytes stripped, result, cost).
- Sandbox backends: local subprocess, Modal sandbox (network blocked, no secrets, skill read-only
  at its git hash, image built from the manifest).
- File stores: local disk, S3-compatible (Cloudflare R2 pricing built in).
- Command-line tool: `skillharness run`, `skillharness show`.
- Example skill: `examples/chart`, matplotlib, one `render` command returning an image.
