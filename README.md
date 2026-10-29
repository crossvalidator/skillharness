# skillharness

Run an [Agent Skill](https://agentskills.io) folder as a bounded, recorded run on any model.

An Agent Skill is a folder with a `SKILL.md`: instructions, plus optional scripts, references and
assets. Claude Code runs one with a shell and a human in the loop. skillharness runs one as a
service: the model gets exactly the tools a small manifest declares, no shell, no network; the skill's
commands run in a sandbox; a hard budget stops the run; every file the run produced is kept, with
what it cost.

```
skillharness run examples/chart "Monthly rainfall in Lisbon" --profile quick --var audience=students
```

Status: being extracted from a production service. The design is in `docs/plan-v1.md`.
