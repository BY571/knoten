"""Attaching files to a node — the code that killed it, the plot that shows why.

Print-free on purpose: the MCP server speaks JSON-RPC over STDOUT, so a stray `print`
here would corrupt every response. This returns data; the CLI does the printing.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .core import FM_RE, GraphError, _Loader, read_frontmatter, split

IMG = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
BIG = 1_000_000          # git is for code and plots, not datasets


@dataclass
class Attached:
    added: list[str] = field(default_factory=list)      # what this call copied in
    embedded: list[str] = field(default_factory=list)   # images written into the body
    warnings: list[str] = field(default_factory=list)


def _node_file(root: Path, nid: str) -> Path:
    nf = root / "nodes" / f"{nid}.md"
    if not nf.exists():
        raise GraphError(f"no node '{nid}'")
    return nf


def _scalar(name: str) -> str:
    """Emit a filename as YAML, quoting it unless it round-trips as the identical string.

    Filenames were written raw. `plot #1.png` became the YAML comment `plot` (and then a
    false missing-attachment violation, with the real file sitting on disk); `run: final
    .png` became a dict; `123` became an int. All are legal filenames.
    """
    try:
        if yaml.load(name, Loader=_Loader) == name:
            return name
    except yaml.YAMLError:
        pass
    return json.dumps(name, ensure_ascii=False)     # a JSON string is valid YAML


def set_list(node_file: Path, names: list[str]) -> None:
    """Rewrite ONLY the `attachments:` block, line by line.

    We do not re-dump the frontmatter through yaml — that would reformat it and strip the
    comments a human wrote.

    The items we must drop may be indented OR NOT: `yaml.dump` emits list items at zero
    indent by default. Requiring leading whitespace orphaned them into the preceding block
    and left the node unparseable — which takes the whole graph down with it, since load()
    raises rather than skips.
    """
    text = node_file.read_text(encoding="utf-8")
    read_frontmatter(node_file)          # refuse to touch a node we cannot parse
    m = FM_RE.match(text)

    kept, dropping = [], False
    for line in m.group(1).splitlines():
        if re.match(r"^attachments:\s*(\[.*\])?\s*$", line):
            dropping = True
            continue
        if dropping:
            if re.match(r"^\s*-\s", line):        # indented or not
                continue
            dropping = False
        kept.append(line)

    if names:
        kept.append("attachments:")
        kept += [f"  - {_scalar(n)}" for n in sorted(names)]
    fm = "\n".join(kept).strip("\n")
    out = f"---\n{fm}\n---\n{m.group(2)}"

    split(out, node_file.name)           # never write a node we cannot read back
    node_file.write_text(out, encoding="utf-8")


def _embed(node_file: Path, nid: str, images: list[str]) -> list[str]:
    """Images render on GitHub only if they are in the body, not just the frontmatter."""
    text = node_file.read_text(encoding="utf-8")
    embedded = []
    for name in images:
        ref = f"![{name}](../attachments/{nid}/{name})"
        if ref in text:
            continue
        if "## Attachments" not in text:
            text = text.rstrip() + "\n\n## Attachments\n"
        text = text.rstrip() + f"\n\n{ref}\n"
        embedded.append(name)
    if embedded:
        node_file.write_text(text, encoding="utf-8")
    return embedded


def _preflight(files: list[str]) -> list[Path]:
    """Vet every file BEFORE copying any, so a bad path halfway through the list cannot
    leave the node half-attached.

    `exists()` is true for a directory, so the old pre-check passed one and copy2 then
    died mid-way, orphaning the files already copied.
    """
    srcs = [Path(f) for f in files]
    for src in srcs:
        if not src.exists():
            raise GraphError(f"no such file: {src}")
        if not src.is_file():
            raise GraphError(f"{src} is not a file")

    names = [s.name for s in srcs]
    if dupes := {n for n in names if names.count(n) > 1}:
        raise GraphError(
            f"two files share the basename {', '.join(sorted(dupes))} — they would land on "
            f"top of each other in attachments/. Rename one.")
    return srcs


def attach(root: Path, nid: str, files: list[str]) -> Attached:
    nf = _node_file(root, nid)
    fm, _ = read_frontmatter(nf)
    names = [str(a) for a in (fm.get("attachments") or [])]
    out = Attached()
    srcs = _preflight(files)

    dest = root / "attachments" / nid
    dest.mkdir(parents=True, exist_ok=True)
    images = []
    for src in srcs:
        if src.is_symlink():
            out.warnings.append(
                f"{src.name} is a symlink to {src.resolve()} — its CONTENTS were copied "
                f"into the graph.")
        if (size := src.stat().st_size) > BIG:
            out.warnings.append(
                f"{src.name} is {size / 1e6:.1f} MB — git is for code and plots, not "
                f"datasets. Consider linking to it instead.")
        shutil.copy2(src, dest / src.name)
        if src.name not in names:
            names.append(src.name)
        out.added.append(src.name)
        if src.suffix.lower() in IMG:
            images.append(src.name)

    set_list(nf, names)
    out.embedded = _embed(nf, nid, images)
    return out


def detach(root: Path, nid: str, name: str) -> None:
    nf = _node_file(root, nid)
    fm, _ = read_frontmatter(nf)
    have = [str(a) for a in (fm.get("attachments") or [])]
    if name not in have:
        raise GraphError(f"'{name}' is not attached to {nid}")

    (root / "attachments" / nid / name).unlink(missing_ok=True)
    set_list(nf, [a for a in have if a != name])

    text = nf.read_text(encoding="utf-8")
    text = re.sub(rf"\n*!\[{re.escape(name)}\]\([^)]*\)\n*", "\n", text)
    text = re.sub(r"\n*## Attachments\s*\n+(?=(##|\Z))", "\n", text)
    nf.write_text(text.rstrip() + "\n", encoding="utf-8")
