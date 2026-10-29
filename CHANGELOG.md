# Changelog

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
