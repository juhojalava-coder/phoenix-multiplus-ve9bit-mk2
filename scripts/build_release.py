"""Builds the release package: dist/phoenix-multiplus-ve9bit-mk2-<version>.zip

Usage:
  python scripts/build_release.py              check the tracked files, then build the zip
  python scripts/build_release.py --check      only check

The check refuses to build if a tracked file contains something that must not be published: private network
addresses, personal paths, hard-coded passwords, third-party binaries or documents, images with EXIF/XMP metadata,
text that is not English, or anything matching your private blocklist (.release_blocklist, untracked). The zip contains the tracked files (git ls-files) in a top-level folder.
"""
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "phoenix-multiplus-ve9bit-mk2"
TEXT_EXT = {".py", ".md", ".toml", ".json", ".jsonl", ".sh", ".bat", ".txt", ".yml", ".yaml", ".cfg"}
# Local recordings that must never be published (mk2_survey.py output, captures). The two reference logs in data/
# are published on purpose and are not matched.
FORBIDDEN_NAMES = re.compile(r"(^|/)(survey_[^/]*\.jsonl|survey_[^/]*_summary\.txt|capture_[^/]*\.log)$")
NO_TEXT_CHECK = {"LICENSE", "scripts/build_release.py"}    # the checker itself contains the patterns
FORBIDDEN_EXT = {".exe", ".dll", ".sys", ".pdf", ".vsc", ".msi", ".zip"}
SKIP_DIRS = ("dist/", ".git/")

FORBIDDEN = [
    (re.compile(r"VE ?Conf|veconf", re.I), "mentions the official configuration software by name"),
    (re.compile(r"MK2_LOYDOKSET"), "refers to a private document"),
    (re.compile(r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"), "private IPv4 address"),
    (re.compile(r"(?i)\b[a-z]:\\users\\|/home/[a-z0-9_.-]+/|\bssh://[^\s/@]+@"), "personal path or ssh account"),
    (re.compile(r"(?i)\bpassword\s*=\s*[\"'][^\"'\s]{3,}[\"']"), "hard-coded password"),
]
NON_ENGLISH = re.compile(r"[äöÄÖåÅ]")

# Personal identifiers (device serials, host names, user names) are NOT stored in the repository. Put one regular
# expression per line in the untracked file .release_blocklist (listed in .gitignore); lines starting with # are comments.
BLOCKLIST_FILE = os.path.join(ROOT, ".release_blocklist")
IMAGE_EXT = {".jpg", ".jpeg", ".tif", ".tiff", ".webp"}


def load_blocklist():
    if not os.path.exists(BLOCKLIST_FILE):
        return []
    with open(BLOCKLIST_FILE, encoding="utf-8") as fh:
        return [re.compile(s, re.I) for s in (ln.strip() for ln in fh) if s and not s.startswith("#")]


def jpeg_has_metadata(data):
    """True if a JPEG carries Exif/XMP/IPTC/comment segments (APP1, APP13, COM)."""
    if data[:2] != b"\xff\xd8":
        return False
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker == 0xDA:                     # start of scan: no more headers
            break
        if marker in (0xE1, 0xED, 0xFE):
            return True
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return False


def git_ls_files():
    git = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"
    r = subprocess.run([git, "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise SystemExit("git ls-files failed: " + r.stderr.strip())
    return [f for f in r.stdout.split("\n") if f and not f.startswith(SKIP_DIRS)]


def check(files):
    problems = []
    blocklist = [(rx, "private identifier (.release_blocklist)") for rx in load_blocklist()]
    for rel in files:
        ext = os.path.splitext(rel)[1].lower()
        if FORBIDDEN_NAMES.search(rel):
            problems.append(f"{rel}: local recording (survey/capture), not for publishing")
            continue
        if ext in FORBIDDEN_EXT:
            problems.append(f"{rel}: third-party or binary file type {ext}")
            continue
        if ext in IMAGE_EXT:
            with open(os.path.join(ROOT, rel), "rb") as fh:
                if jpeg_has_metadata(fh.read()):
                    problems.append(f"{rel}: image contains metadata (EXIF/XMP/IPTC/comment); strip it before release")
            continue
        if rel in NO_TEXT_CHECK or (ext not in TEXT_EXT and not rel.startswith("data/")):
            continue
        # data/ holds recorded serial traffic: scan it only for the personal blocklist, not for the text rules
        patterns = blocklist if rel.startswith("data/") else FORBIDDEN + blocklist
        with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh, 1):
                for rx, why in patterns:
                    if rx.search(line):
                        problems.append(f"{rel}:{n}: {why}: {line.strip()[:80]}")
                if not rel.startswith("data/") and NON_ENGLISH.search(line):
                    problems.append(f"{rel}:{n}: non-English text: {line.strip()[:80]}")
    return problems


def build(files):
    with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as fh:
        version = fh.read().strip()
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    top = f"{NAME}-{version}"
    path = os.path.join(out_dir, top + ".zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            if rel.startswith(".git"):
                continue                       # .gitignore / .gitattributes are repository files
            z.write(os.path.join(ROOT, rel), f"{top}/{rel}")
    return path, len(files)


def main():
    files = git_ls_files()
    problems = check(files)
    if problems:
        print("Release check FAILED:")
        for p in problems:
            print("  " + p)
        return 1
    print(f"Release check passed ({len(files)} tracked files).")
    if "--check" in sys.argv:
        return 0
    path, n = build(files)
    print(f"Built {path} ({os.path.getsize(path) // 1024} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
