"""A skill folder: SKILL.md parsed, the readable files resolved, the git hash recorded.

The hash is stamped on every run so a later skill change never silently alters an old result.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .contract import SkillRef
from .manifest import Manifest, load_manifest

FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.S)
SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MANIFEST_FILE = "harness.yaml"


def parse_skill_md(text: str) -> tuple[dict[str, Any], str]:
    """Frontmatter mapping and the Markdown body."""
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    data = yaml.safe_load(match.group(1)) or {}
    if not isinstance(data, dict):
        raise ValueError("SKILL.md frontmatter must be a mapping")
    return data, text[match.end():]


@dataclass(frozen=True)
class Skill:
    name: str
    folder: Path
    description: str
    body: str  # SKILL.md without the frontmatter: the model's instructions
    frontmatter: dict[str, Any]
    readable: tuple[str, ...]  # paths relative to `folder` that read_file may open
    git_hash: str
    manifest: Manifest

    def read(self, relative: str) -> str:
        if relative not in self.readable:
            raise FileNotFoundError(relative)
        return (self.folder / relative).read_text(encoding="utf-8")

    @property
    def ref(self) -> SkillRef:
        return SkillRef(name=self.name, git_hash=self.git_hash)


def readable_files(folder: Path, patterns: list[str]) -> tuple[str, ...]:
    found: set[str] = set()
    for pattern in patterns:
        for p in folder.glob(pattern):
            if p.is_file() and folder in p.resolve().parents:
                found.add(str(p.relative_to(folder)))
    return tuple(sorted(found))


def load_skill(folder: Path, manifest: Manifest | Path | None = None, *, git_hash: str | None = None) -> Skill:
    """Load the skill at `folder`.

    `manifest`: a loaded Manifest, a path to one, or None for `harness.yaml` inside the folder.
    `git_hash` overrides the git lookup where the folder is a pinned copy outside a repository.
    """
    folder = folder.resolve()
    skill_md = folder / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"no SKILL.md in {folder}")
    frontmatter, body = parse_skill_md(skill_md.read_text(encoding="utf-8"))
    name = str(frontmatter.get("name") or folder.name)
    if not SKILL_NAME.match(name) or len(name) > 64:
        raise ValueError(f"bad skill name {name!r} (lowercase letters, digits and single hyphens)")
    if isinstance(manifest, Manifest):
        spec = manifest
    else:
        path = manifest or folder / MANIFEST_FILE
        if not path.is_file():
            raise FileNotFoundError(f"no manifest at {path}; a skill needs harness.yaml or one supplied by the caller")
        spec = load_manifest(path)
    return Skill(
        name=name,
        folder=folder,
        description=str(frontmatter.get("description", "")),
        body=body.strip(),
        frontmatter=frontmatter,
        readable=readable_files(folder, spec.readable),
        git_hash=git_hash or git_hash_of(folder),
        manifest=spec,
    )


def git_hash_of(folder: Path) -> str:
    """Last commit touching the folder, with "-dirty" when it has uncommitted changes."""
    try:
        head = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", str(folder)],
            cwd=folder, capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", str(folder)],
            cwd=folder, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
    if not head:
        return "uncommitted"
    return f"{head}-dirty" if dirty else head


def export_at_hash(folder: Path, git_hash: str, dest_root: Path) -> Path:
    """Copy the skill folder as it is at its committed git hash into `dest_root/<hash>/`.

    `git archive` reads the commit, not the working tree, so uncommitted edits never reach an image.
    Returns the exported folder."""
    commit = git_hash.removesuffix("-dirty")
    if commit in ("unknown", "uncommitted"):
        raise RuntimeError(f"the skill folder has no git hash to pin ({git_hash})")

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=folder, capture_output=True, text=True, check=True).stdout.strip()

    prefix = git("rev-parse", "--show-prefix").rstrip("/")
    top = git("rev-parse", "--show-toplevel")
    dest = dest_root / commit
    out = dest / prefix if prefix else dest
    if out.is_dir() and (out / "SKILL.md").is_file():
        return out
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", commit, *([prefix] if prefix else [])], cwd=top, capture_output=True, check=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)
    return out
