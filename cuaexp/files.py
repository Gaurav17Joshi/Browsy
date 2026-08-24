"""Local file access, fenced to one directory.

The fence is the whole point. Browsy reads untrusted text off web pages as a
matter of course -- that is its job -- so anything it can be talked into doing
is something a web page can do. A path allowlist enforced here, in code, is the
only kind that survives a model being talked round by injected page text; a rule
in the prompt is not a rule.

Everything resolves through Path.resolve() BEFORE the containment check, so
`..`, a symlink pointing out of the root, and a bare absolute path all collapse
to a real location that either is or is not inside the fence.

Reads come from CUAEXP_FILE_ROOT (default <project>/workspace). Writes go only
to <root>/output. Put a file in the workspace to let Browsy see it; that opt-in
is deliberate and is the security model.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .config import FILE_OUT, FILE_ROOT, KEYFILE, MAX_FILE_BYTES, READ_ROOTS, SKILLS_DIR

log = logging.getLogger("cuaexp.files")


class FileDenied(Exception):
    """Refused by policy. The message is shown to the model verbatim."""


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def _check(path: str, roots: list[Path] | Path, what: str) -> Path:
    """Resolve `path` and prove it lands inside one of `roots`, or refuse.

    A relative path is tried against each root in turn and the first that both
    contains it and exists wins; failing that, the first root, so the error names
    somewhere sensible. An absolute path just has to land inside one of them.
    """
    if isinstance(roots, Path):
        roots = [roots]
    if not path or not path.strip():
        raise FileDenied("no path given")
    raw = Path(path.strip().strip('"').strip("'")).expanduser()

    resolved, escaped = None, False
    for root in roots:
        target = raw if raw.is_absolute() else (root / raw)
        try:
            cand = target.resolve()
        except (OSError, ValueError) as e:
            raise FileDenied(f"cannot resolve that path: {e}") from e
        if not _inside(cand, root):
            escaped = True
            continue
        if cand.exists():
            resolved = cand
            break
        if resolved is None:
            resolved = cand       # first plausible location, for the error message
    # An escape must be reported AS an escape. Without this the path that broke
    # out of one root fell through to the next, where the same relative name
    # merely does not exist -- so a traversal attempt came back as a bland "no
    # such file" pointing at a directory it never touched.
    if resolved is None or (escaped and not resolved.exists()):
        shown = " or ".join(str(r.resolve()) for r in roots)
        raise FileDenied(
            f"DENIED: that path is outside the {what} directory ({shown}). "
            f"Only paths inside it are allowed. If the user wants a file read, "
            f"they must place it there themselves -- do not ask them to move the "
            f"fence, and do not try another path.")

    # Defence in depth: never hand back the API key even if someone points the
    # root at the directory holding it.
    try:
        if resolved == KEYFILE.resolve():
            raise FileDenied("DENIED: that file holds the API key and is never readable.")
    except (OSError, ValueError):
        pass
    return resolved


def read_file(path: str) -> str:
    p = _check(path, READ_ROOTS, "workspace or skills")
    if not p.exists():
        raise FileDenied(f"no such file: {p} (readable roots are "
                         f"{FILE_ROOT.resolve()} and {SKILLS_DIR.resolve()})")
    if p.is_dir():
        names = sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir())
        return f"{p} is a directory containing: " + (", ".join(names[:200]) or "(empty)")
    size = p.stat().st_size
    if size > MAX_FILE_BYTES:
        raise FileDenied(
            f"that file is {size:,} bytes, over the {MAX_FILE_BYTES:,} limit. "
            f"Read a smaller file, or ask the user to split it.")
    raw = p.read_bytes()
    if b"\x00" in raw[:8192]:
        raise FileDenied(
            f"{p.name} looks binary, so there is nothing useful to read as text. "
            f"It is {size:,} bytes.")
    log.info("read %s (%s bytes)", p, size)
    return raw.decode("utf-8", "replace")


def write_file(path: str, content: str, append: bool = False) -> str:
    """Write (or append to) a file in the output directory. Returns the path."""
    p = _check(path, FILE_OUT, "output")
    incoming = len(content.encode("utf-8", "replace"))
    existing = p.stat().st_size if (append and p.exists()) else 0
    # The cap is on the resulting file, not on one call -- otherwise appending
    # in small pieces walks straight past it.
    if existing + incoming > MAX_FILE_BYTES:
        raise FileDenied(
            f"that would make the file {existing + incoming:,} bytes, over the "
            f"{MAX_FILE_BYTES:,} limit. Start a new file instead.")
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a" if append else "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    log.info("%s %s (now %s bytes)", "appended to" if append else "wrote",
             p, p.stat().st_size)
    return str(p)


def as_url(path: str) -> str:
    """file:// URL for something already written, so a tab can open it."""
    return _check(path, FILE_OUT, "output").as_uri()
