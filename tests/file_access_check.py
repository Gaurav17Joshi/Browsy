"""Does the file fence hold?

The threat is not a careless user, it is a web page. Browsy reads untrusted text
for a living, so assume the model has been talked into trying every one of these
and check that the code says no anyway.

    .venv\\Scripts\\python.exe tests\\file_access_check.py
"""
from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cuaexp.config import (FILE_OUT, FILE_ROOT, KEYFILE, MAX_FILE_BYTES,  # noqa: E402
                           SKILLS_DIR)
from cuaexp.files import FileDenied, read_file, write_file  # noqa: E402

rows: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    rows.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


def denied(name: str, fn) -> None:
    """The call must be refused by policy, not merely fail."""
    try:
        out = fn()
    except Exception as e:
        # By class name, not isinstance: the keyfile case reloads cuaexp.files,
        # which rebinds FileDenied to a new class object and would otherwise make
        # every later refusal look like the wrong exception.
        ok = type(e).__name__ == "FileDenied"
        check(name, ok, (str(e) if ok else f"wrong exception {type(e).__name__}: {e}")[:70])
    else:
        check(name, False, f"ALLOWED, returned {str(out)[:60]!r}")


def allowed(name: str, fn, expect=None) -> None:
    try:
        out = fn()
    except Exception as e:
        check(name, False, f"refused: {type(e).__name__}: {e}"[:90])
        return
    check(name, expect is None or expect in str(out), str(out)[:60])


def main() -> int:
    FILE_ROOT.mkdir(parents=True, exist_ok=True)
    FILE_OUT.mkdir(parents=True, exist_ok=True)
    (FILE_ROOT / "hello.txt").write_text("hello from the workspace", encoding="utf-8")

    print("\n-- the happy path")
    allowed("a file in the workspace reads", lambda: read_file("hello.txt"),
            "hello from the workspace")
    allowed("the workspace lists as a directory", lambda: read_file("."), "directory")
    allowed("a file writes to output", lambda: write_file("report.html", "<h1>hi</h1>"),
            "report.html")
    allowed("and reads back", lambda: read_file("output/report.html"), "<h1>hi</h1>")
    allowed("subdirectories are created", lambda: write_file("a/b/c.txt", "deep"), "c.txt")

    print("\n-- escaping the fence")
    allowed("a skill reads by bare name", lambda: read_file("web-design.md"),
            "single-file web page")
    allowed("the skills directory lists", lambda: read_file(str(SKILLS_DIR)), "directory")
    denied("writing into the skills directory",
           lambda: write_file(str(SKILLS_DIR / "injected.md"), "x"))
    denied("traversing out of the skills root", lambda: read_file("../prompt.txt"))

    denied("relative traversal", lambda: read_file("../cuaexp/config.py"))
    denied("doubled traversal", lambda: read_file("../../../../Windows/System32/drivers/etc/hosts"))
    denied("traversal buried mid-path", lambda: read_file("sub/../../cuaexp/keyfile.py"))
    denied("absolute path outside", lambda: read_file(str(ROOT / "cuaexp" / "config.py")))
    denied("home-relative path", lambda: read_file("~/.ssh/id_rsa"))
    denied("writing outside output", lambda: write_file("../escaped.txt", "x"))
    denied("writing to an absolute path", lambda: write_file(str(ROOT / "pwned.txt"), "x"))
    denied("writing over a source file",
           lambda: write_file(str(ROOT / "cuaexp" / "config.py"), "x"))
    denied("empty path", lambda: read_file(""))
    denied("whitespace path", lambda: read_file("   "))

    print("\n-- the key")
    denied("the API key file, by absolute path", lambda: read_file(str(KEYFILE)))
    # And with the fence moved to sit right on top of it, which is the case the
    # containment check alone would not catch.
    import importlib
    os.environ["CUAEXP_FILE_ROOT"] = str(KEYFILE.parent)
    import cuaexp.config as cfg
    import cuaexp.files as fl
    importlib.reload(cfg)
    importlib.reload(fl)
    try:
        denied("the API key file, with the fence around it",
               lambda: fl.read_file(KEYFILE.name))
    finally:
        os.environ.pop("CUAEXP_FILE_ROOT", None)
        importlib.reload(cfg)
        importlib.reload(fl)

    print("\n-- symlinks")
    link = FILE_ROOT / "escape_link"
    made = False
    try:
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(ROOT / "cuaexp")
        made = True
    except (OSError, NotImplementedError):
        # Windows needs admin or Developer Mode for a real symlink, but a
        # directory junction needs neither and resolve() follows it just the
        # same -- so the escape route still gets tested rather than skipped.
        import subprocess
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(ROOT / "cuaexp")],
                           capture_output=True, text=True)
        made = r.returncode == 0
        if not made:
            check("symlink out of the workspace is refused", False,
                  "could not create a symlink OR a junction -- path untested")
    if made:
        denied("a link pointing out of the workspace",
               lambda: read_file("escape_link/config.py"))
        try:
            link.unlink()
        except OSError:
            link.rmdir()          # junctions unlink as directories

    print("\n-- size and shape")
    big = FILE_ROOT / "big.txt"
    big.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    denied("a file over the size cap", lambda: read_file("big.txt"))
    big.unlink()
    binary = FILE_ROOT / "thing.bin"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 400)
    denied("a binary file", lambda: read_file("thing.bin"))
    binary.unlink()
    denied("content over the size cap",
           lambda: write_file("huge.txt", "y" * (MAX_FILE_BYTES + 1)))

    print("\n-- missing things")
    denied("a file that is not there", lambda: read_file("nope.txt"))

    bad = [r for r in rows if not r[1]]
    print("\n" + "=" * 64)
    print(f"  {len(rows) - len(bad)}/{len(rows)} passed, {len(bad)} failed")
    print("=" * 64)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
