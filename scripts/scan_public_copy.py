"""Search a public candidate folder and its Git history for private information.

Run before every push to the public repository (recommendation R5):

    .venv\\Scripts\\python.exe scripts\\scan_public_copy.py <public folder>

What is searched
    - every file in the working tree (text and binary, so screenshots and PDFs are
      included), except ``.git``, virtual environments and caches;
    - with a Git repository: every blob reachable from any ref (so a secret removed in a
      later commit is still found), and every commit's author, committer and message.

What is looked for
    - **private patterns**: regular expressions in ``local/public_scan_patterns.txt``
      (one per line, ``#`` comments). ``local/`` is git-ignored, so names, paths,
      serial numbers and dataset file names are never published by the scanner itself.
      Matches of private patterns are reported by pattern number only, never echoed.
    - **generic patterns**: credentials (cloud keys, tokens, private-key blocks,
      assigned passwords), user-profile paths, and e-mail addresses other than
      no-reply and example domains.

Reviewed false positives
    ``local/public_scan_allow.txt``: lines ``<pattern id> <path glob>``, e.g.
    ``email docs/CITATION.cff``. Each line should carry a ``#`` reason.

Exit status: 0 nothing found, 1 findings, 2 configuration error. This scanner
complements, and does not replace, GitHub secret scanning and Gitleaks.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATTERNS = ROOT / "local" / "public_scan_patterns.txt"
DEFAULT_ALLOW = ROOT / "local" / "public_scan_allow.txt"
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".ruff_cache",
             ".pytest_cache", "graphify-out"}  # fmt: skip
MAX_BYTES = 50 * 1024 * 1024

ALLOWED_EMAIL = re.compile(
    rb"(@users\.noreply\.github\.com|^noreply@github\.com|@example\.(com|org|net)"
    rb"|^noreply@anthropic\.com)$",
    re.IGNORECASE,
)
GENERIC: dict[str, re.Pattern[bytes]] = {
    "aws-key": re.compile(rb"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github-token": re.compile(rb"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})"),
    "slack-token": re.compile(rb"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "google-key": re.compile(rb"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "openai-anthropic-key": re.compile(rb"\bsk-(ant-)?[A-Za-z0-9_\-]{32,}"),
    "private-key": re.compile(rb"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY"),
    "password-assignment": re.compile(
        rb"(?i)\b(password|passwd|pwd|secret|api_?key|token)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"
    ),
    "user-profile-path": re.compile(
        rb"(?i)([A-Z]:[\\/]+Users[\\/]+(?!Public\b|Default\b|<|\{|\$|%|USER|NAME)[A-Za-z0-9._-]+"
        # not after a host name or path segment, so URLs such as example.org/home/... pass
        rb"|(?<![\w.])/home/(?!user\b|runner\b|<)[a-z0-9._-]+"
        rb"|(?<![\w.])/Users/(?!Shared\b|runner\b|<)[A-Za-z0-9._-]+)"
    ),
    "email": re.compile(rb"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
}


@dataclass(frozen=True)
class Finding:
    where: str  # file path, or "git:<blob id> <path>", or "git:commit <id>"
    pattern: str
    line: int
    excerpt: str  # empty for private patterns


def load_private(path: Path) -> list[re.Pattern[bytes]]:
    patterns = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        text = raw.strip()
        if text and not text.startswith("#"):
            patterns.append(re.compile(text.encode("utf-8"), re.IGNORECASE))
    return patterns


def load_allow(path: Path | None) -> list[tuple[str, str]]:
    if path is None or not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        text = raw.split("#", 1)[0].strip()
        if text:
            pattern_id, glob = text.split(maxsplit=1)
            out.append((pattern_id, glob))
    return out


def scan_bytes(data: bytes, where: str, private: list[re.Pattern[bytes]]) -> Iterator[Finding]:
    for number, line in enumerate(data.splitlines(), start=1):
        for i, pattern in enumerate(private, start=1):
            if pattern.search(line):
                yield Finding(where, f"private-{i}", number, "")
        for name, pattern in GENERIC.items():
            for match in pattern.finditer(line):
                if name == "email" and ALLOWED_EMAIL.search(match.group(0)):
                    continue
                excerpt = match.group(0)[:60].decode("utf-8", "replace")
                yield Finding(where, name, number, excerpt)


def _git_ignored(folder: Path) -> set[str]:
    """Files Git ignores in ``folder`` (they cannot be committed, e.g. ``.coverage``)."""
    if not (folder / ".git").exists():
        return set()
    out = _git(folder, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
    return {p.decode("utf-8", "replace") for p in out.split(b"\x00") if p}


def working_tree(folder: Path) -> Iterator[tuple[str, bytes]]:
    ignored = _git_ignored(folder)
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if any(part in SKIP_DIRS for part in rel.parts) or not path.is_file():
            continue
        if rel.as_posix() in ignored:
            continue
        if path.stat().st_size <= MAX_BYTES:
            yield rel.as_posix(), path.read_bytes()


def _git(folder: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(  # noqa: S603 - fixed argument list, no shell
        ["git", "-C", str(folder), *args],  # noqa: S607 - git from PATH by design
        input=data,
        check=True,
        capture_output=True,
    ).stdout


def git_history(folder: Path) -> Iterator[tuple[str, bytes]]:
    """Every reachable blob (with one path it had) and every commit's metadata."""
    listing = _git(folder, "rev-list", "--objects", "--all").decode("utf-8", "replace")
    paths: dict[str, str] = {}
    for row in listing.splitlines():
        oid, _, name = row.partition(" ")
        paths.setdefault(oid, name)
    if paths:
        kinds = _git(folder, "cat-file", "--batch-check", data="\n".join(paths).encode())
        blobs = [r.split()[0].decode() for r in kinds.splitlines() if r.split()[1] == b"blob"]
        for oid in blobs:
            size = int(_git(folder, "cat-file", "-s", oid))
            if size <= MAX_BYTES:
                yield f"git:{oid[:12]} {paths[oid]}", _git(folder, "cat-file", "blob", oid)
    log = _git(folder, "log", "--all", "--format=%x00%H%n%an <%ae>%n%cn <%ce>%n%B")
    for entry in log.split(b"\x00")[1:]:
        commit, _, rest = entry.partition(b"\n")
        yield f"git:commit {commit.decode()[:12]}", rest


def is_allowed(finding: Finding, allow: list[tuple[str, str]]) -> bool:
    path = finding.where.split(" ", 1)[-1] if finding.where.startswith("git:") else finding.where
    return any(pid == finding.pattern and fnmatch.fnmatch(path, glob) for pid, glob in allow)


def scan(
    folder: Path, private: list[re.Pattern[bytes]], allow: list[tuple[str, str]]
) -> list[Finding]:
    sources: list[Iterator[tuple[str, bytes]]] = [working_tree(folder)]
    if (folder / ".git").exists():
        sources.append(git_history(folder))
    found: list[Finding] = []
    for source in sources:
        for where, data in source:
            found.extend(f for f in scan_bytes(data, where, private) if not is_allowed(f, allow))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--patterns", type=Path, default=DEFAULT_PATTERNS)
    parser.add_argument("--allow", type=Path, default=DEFAULT_ALLOW)
    parser.add_argument(
        "--no-private", action="store_true", help="run only the generic patterns (not a gate)"
    )
    args = parser.parse_args(argv)
    if not args.folder.is_dir():
        print(f"not a folder: {args.folder}", file=sys.stderr)
        return 2
    if args.no_private:
        private: list[re.Pattern[bytes]] = []
    elif not args.patterns.exists():
        print(f"private pattern file missing: {args.patterns}", file=sys.stderr)
        return 2
    else:
        private = load_private(args.patterns)
    findings = scan(args.folder.resolve(), private, load_allow(args.allow))
    for f in findings:
        detail = f" {f.excerpt!r}" if f.excerpt else ""
        print(f"{f.pattern}: {f.where}:{f.line}{detail}")
    print(f"{len(findings)} finding(s); {len(private)} private pattern(s) used")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
