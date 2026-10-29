from __future__ import annotations

import pytest

from skillharness.manifest import Manifest
from skillharness.skill import parse_skill_md

MINIMAL = """
artifact: {name: out.md}
profiles:
  quick: {runs: 1, seconds: 60, dollars: 0.1}
"""


def test_minimal_manifest_has_no_commands():
    m = Manifest.from_yaml(MINIMAL)
    assert m.commands == {} and m.required_commands == []
    b = m.budget("quick")
    assert b.runs == 1 and b.request_limit == 7 and b.options == set() and b.effort == "high"
    with pytest.raises(KeyError):
        m.budget("deep")


def test_option_argv_and_shorthand_extra():
    m = Manifest.from_yaml(
        """
artifact: {name: a.js}
commands:
  build:
    description: d
    run: "node {skill}/b.mjs {artifact}"
    options:
      state: {type: string, argv: "--state {value}"}
      zoom: {type: flag, argv: "--zoom"}
finish:
  extra: {skipped: "why"}
profiles:
  p: {runs: 2, seconds: 10, dollars: 1, options: [state, zoom]}
"""
    )
    opts = m.commands["build"].options
    assert opts["state"].argv_for("seat=2,air=true") == ["--state", "seat=2,air=true"]
    assert opts["state"].argv_for("") == []
    assert opts["zoom"].argv_for(True) == ["--zoom"] and opts["zoom"].argv_for(False) == []
    assert m.finish.extra["skipped"].type == "string" and m.finish.extra["skipped"].description == "why"


@pytest.mark.parametrize(
    "bad, message",
    [
        ("artifact: {name: a}\nprofiles: {}", "at least one profile"),
        ("artifact: {name: a}\ncommands: {finish: {description: d, run: x}}\nprofiles: {p: {runs: 1, seconds: 1, dollars: 1}}", "bad command name"),
        ("artifact: {name: a}\nprofiles: {p: {runs: 1, seconds: 1, dollars: 1, options: [zoom]}}", "no command has"),
        ("artifact: {name: a}\nreadable: ['../x']\nprofiles: {p: {runs: 1, seconds: 1, dollars: 1}}", "inside the skill folder"),
    ],
)
def test_manifest_rejects(bad: str, message: str):
    with pytest.raises(ValueError, match=message):
        Manifest.from_yaml(bad)


def test_frontmatter():
    fm, body = parse_skill_md("---\nname: chart\ndescription: >\n  two\n  lines\n---\n# Title\n\nbody\n")
    assert fm == {"name": "chart", "description": "two lines"} and body.startswith("# Title")
    assert parse_skill_md("no frontmatter") == ({}, "no frontmatter")
