#!/usr/bin/env python3
"""The watch (.github/workflows/watch.yml): once a day, has DOG Mode or Bitcoin Core moved past what this repository builds?

It reads, with the workflow's own token: DOG Mode's branch 31.1-dogmode (its tip, and how the tip compares with the
commit this repository pins), DOG Mode's version at that tip (CMakeLists.txt), DOG Mode's tags and releases, and
Bitcoin Core's tags and releases. It opens one issue for each new thing, and never a second issue with a title it has
opened before, open or closed (only issues this workflow opened count, so nobody else's issue can silence one):

  1. the branch holds commits the pin does not (the title names the tip);
  2. DOG Mode's repository has a tag or release that is not one of Bitcoin Core's own tags;
  3. Bitcoin Core released a version in the line DOG Mode is built on, newer than DOG Mode's base;
  4. Bitcoin Core released a newer major than DOG Mode's base, for information;
  5. the Dockerfile and build.yml pin different commits.

Issue text is built from commit ids, dates, version numbers and tag names only. Nothing written upstream (a commit
message, a release title or note) is copied in, and a tag name that is not plain text is shown by its hash.

A read that fails, or an answer in a shape this script does not expect, stops it with an error before any issue is
opened, so the run fails (GitHub emails a failed scheduled run) and never reports "nothing new" it did not see.

    python3 .github/watch.py             # in the workflow: GH_TOKEN and GITHUB_REPOSITORY come from the runner
    python3 .github/watch.py --dry-run   # anywhere: prints what it would open and opens nothing (GH_TOKEN, or gh's login)

Standard library only.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.github.com"
WEB = "https://github.com"
DOGMODE = "bitcoindogmode/bitcoin"
BRANCH = "31.1-dogmode"
CORE = "bitcoin/bitcoin"
BOT = "github-actions[bot]"   # the author of every issue opened with a workflow's own token
UA = "dogofbitcoin-docker-dogmode-watch/1.0"
DEFAULT_REPO = "dogofbitcoin/docker-dogmode"
# More new things than this in one run means a read went wrong, not that upstream moved: nothing is opened.
MAX_NEW_TAGS = 10
MAX_NEW_ISSUES = 6

SHA = re.compile(r"[0-9a-f]{40}")
DATE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
PLAIN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+/-]{0,99}")
FINAL = re.compile(r"v(\d+)\.(\d+)(?:\.(\d+))?")
REPO_NAME = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")


class WatchError(Exception):
    """A read failed, or came back in a shape this script does not expect."""


def need(ok, what):
    if not ok:
        raise WatchError(what)


def one_line(text, limit=200):
    return " ".join(str(text).split())[:limit]


def make_api(token):
    def api(path, method="GET", body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"{API}/{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", UA)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise WatchError(f"{method} {path}: HTTP {e.code} {one_line(e.read().decode(errors='replace'))}") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise WatchError(f"{method} {path}: {one_line(e)}") from None
    return api


def paged(api, path, pages=20):
    out = []
    sep = "&" if "?" in path else "?"
    for page in range(1, pages + 1):
        got = api(f"{path}{sep}per_page=100&page={page}")
        need(isinstance(got, list), f"GET {path}: not a list")
        out += got
        if len(got) < 100:
            return out
    raise WatchError(f"GET {path}: more than {pages} pages")


# ---- the pure part: what was read, and what it means (tests/test_docker_dogmode_watch.py) ----

def parse_pins(dockerfile, workflow):
    """(the Dockerfile's default DOGMODE_COMMIT, build.yml's env DOGMODE_COMMIT); build.yml passes its own to the build."""
    a = re.findall(r"^ARG DOGMODE_COMMIT=([0-9a-f]{40})\s*$", dockerfile, re.M)
    b = re.findall(r"^\s+DOGMODE_COMMIT:\s*([0-9a-f]{40})\s*$", workflow, re.M)
    need(len(a) == 1, "the Dockerfile should carry exactly one ARG DOGMODE_COMMIT=<40 hex>")
    need(len(b) == 1, "build.yml should carry exactly one DOGMODE_COMMIT: <40 hex>")
    return a[0], b[0]


def parse_base(cmake):
    """DOG Mode's own version, the Bitcoin Core release it is built on, as (major, minor, build)."""
    got = []
    for key in ("MAJOR", "MINOR", "BUILD"):
        m = re.findall(rf"^set\(CLIENT_VERSION_{key} (\d+)\)\s*$", cmake, re.M)
        need(len(m) == 1, f"CMakeLists.txt at the tip should set CLIENT_VERSION_{key} once")
        got.append(int(m[0]))
    return tuple(got)


def core_final(tag):
    """(major, minor, build) for a final release tag such as v31.1 or v29.4; None for a release candidate or anything else."""
    m = FINAL.fullmatch(tag or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def vstr(v):
    return f"{v[0]}.{v[1]}" + (f".{v[2]}" if v[2] else "")


def tag_names(refs, repo):
    need(isinstance(refs, list) and refs, f"{repo}'s tags came back empty or not a list")
    names = set()
    for r in refs:
        ref = r.get("ref") if isinstance(r, dict) else None
        need(isinstance(ref, str) and ref.startswith("refs/tags/"), f"{repo}: a tag in an unexpected shape")
        names.add(ref[len("refs/tags/"):])
    return names


def releases(items, repo):
    """{tag: {published_at, prerelease}} for every published (not draft) release; titles and notes are never read."""
    out = {}
    for r in items:
        need(isinstance(r, dict) and isinstance(r.get("tag_name"), str) and r["tag_name"], f"{repo}: a release without a tag")
        if r.get("draft"):
            continue
        when = r.get("published_at")
        need(when is None or (isinstance(when, str) and DATE.fullmatch(when)), f"{repo}: a release date in an unexpected shape")
        out[r["tag_name"]] = {"published_at": when, "prerelease": bool(r.get("prerelease"))}
    return out


def core_finals(rel):
    """[(version, tag, published_at)] for Bitcoin Core's final releases; release candidates and pre-releases are not."""
    return sorted((core_final(t), t, v["published_at"]) for t, v in rel.items()
                  if not v["prerelease"] and core_final(t) and v["published_at"])


def label(name):
    return name if PLAIN_NAME.fullmatch(name) else f"(a name that is not plain text, sha256 {hashlib.sha256(name.encode()).hexdigest()[:12]})"


def new_tags(r):
    return sorted((set(r["dog_tags"]) | set(r["dog_releases"])) - set(r["core_tags"]))


def commit_link(sha):
    return f"[`{sha}`]({WEB}/{DOGMODE}/commit/{sha})"


def commits(n):
    return f"{n} commit" + ("" if n == 1 else "s")


def run_link(repo, run_id):
    """The link to this run, from the repository the issues open in and the runner's run id, or "" when either is not
    plain. The pure part never reads the environment: main() passes the run id, so a test run elsewhere links nothing."""
    if str(run_id or "").isdigit() and REPO_NAME.fullmatch(repo or ""):
        return f"{WEB}/{repo}/actions/runs/{run_id}"
    return ""


def closing(r, tip=True, link=""):
    run = f" ([this run]({link}))" if link else ""
    return ("What happens next: a new DOG Mode commit or release is built, tested and run on the Dog of Bitcoin "
            "Foundation's own node first, by its maintenance process. This repository's pin moves after that, and "
            "its image is rebuilt only by a pushed tag.\n\n"
            f"Opened by `.github/workflows/watch.yml`{run}, which reads and opens issues and does nothing else."
            + (f" DOG Mode's branch `{BRANCH}` was at {commit_link(r['tip'])} when it read." if tip else ""))


def events(r, link=""):
    """[{title, body}] for everything new in a reading, in a fixed order. Titles are the dedupe keys. link is this run's
    page (run_link), or "" for none."""
    out = []
    base = r["base"]
    pins = f"The Dockerfile pins {commit_link(r['docker_pin'])}; build.yml pins {commit_link(r['workflow_pin'])}."
    if r["docker_pin"] == r["workflow_pin"]:
        pins = f"This repository builds DOG Mode at {commit_link(r['workflow_pin'])}."

    if r["docker_pin"] != r["workflow_pin"]:
        out.append({
            "title": f"The image's two pins disagree: Dockerfile {r['docker_pin'][:12]}, build.yml {r['workflow_pin'][:12]}",
            "body": (f"The Dockerfile's `ARG DOGMODE_COMMIT` is {commit_link(r['docker_pin'])} and build.yml's "
                     f"`DOGMODE_COMMIT` is {commit_link(r['workflow_pin'])}. build.yml passes its own to the build, so a "
                     "published image builds the second and a local `docker build` builds the first. They should be one "
                     "commit.\n\n" + closing(r, link=link))})

    moved = {p: c for p, c in sorted(r["compares"].items()) if c["status"] in ("ahead", "diverged")}
    if moved:
        lines = []
        for pin, c in moved.items():
            cmp_link = f"[compare]({WEB}/{DOGMODE}/compare/{pin}...{r['tip']})"
            if c["status"] == "ahead":
                lines.append(f"- against {commit_link(pin)}: {commits(c['ahead_by'])} ahead ({cmp_link})")
            else:
                lines.append(f"- against {commit_link(pin)}: diverged, {commits(c['ahead_by'])} the pin does not hold "
                             f"and {commits(c['behind_by'])} the branch does not ({cmp_link})")
        out.append({
            "title": f"DOG Mode's branch {BRANCH} moved to {r['tip'][:12]}",
            "body": (f"DOG Mode's branch [`{BRANCH}`]({WEB}/{DOGMODE}/tree/{BRANCH}) is at {commit_link(r['tip'])}, "
                     f"committed {r['tip_date']}, and holds commits this repository's pin does not.\n\n" + "\n".join(lines)
                     + f"\n\nDOG Mode's version at the tip: Bitcoin Core {vstr(base)}.\n\n" + closing(r, tip=False, link=link))})

    for name in new_tags(r):
        rel = r["dog_releases"].get(name)
        if PLAIN_NAME.fullmatch(name):
            title = f"DOG Mode's repository has a new tag: {name}"
            where = f"[`{name}`]({WEB}/{DOGMODE}/releases/tag/{urllib.parse.quote(name, safe='/')})"
        else:
            title = f"DOG Mode's repository has a new tag {label(name)}"
            where = f"one whose name is not plain text, sha256 `{hashlib.sha256(name.encode()).hexdigest()}` ([tags]({WEB}/{DOGMODE}/tags))"
        if rel is None:
            said = "No release is published for it."
        elif rel["prerelease"]:
            said = f"A release is published for it on {rel['published_at']}, marked as a pre-release."
        else:
            said = f"A release is published for it on {rel['published_at'] or 'a date GitHub does not give'}."
        out.append({"title": title, "body": (
            f"[{DOGMODE}]({WEB}/{DOGMODE}) has a tag that is not one of Bitcoin Core's own: {where}. {said}\n\n"
            f"{pins}\n\n" + closing(r, link=link))})

    finals = r["core_finals"]
    line = [f for f in finals if f[0][0] == base[0]]
    if line and max(line)[0] > base:
        v, tag, when = max(line)
        out.append({"title": f"Bitcoin Core {vstr(v)} released, newer than DOG Mode's base", "body": (
            f"Bitcoin Core [{vstr(v)}]({WEB}/{CORE}/releases/tag/{tag}) was published {when}. DOG Mode's branch is built "
            f"on Bitcoin Core {vstr(base)} (CMakeLists.txt at {commit_link(r['tip'])}). A release in the line DOG Mode "
            "sits on reaches DOG Mode's users once the DOG Mode maintainers move the branch onto it.\n\n"
            f"{pins}\n\n" + closing(r, link=link))})

    for major in sorted({f[0][0] for f in finals if f[0][0] > base[0]}):
        first = min(f for f in finals if f[0][0] == major)
        newest = max(f for f in finals if f[0][0] == major)
        out.append({"title": f"Bitcoin Core {vstr(first[0])} released, a newer major than DOG Mode's base", "body": (
            f"For information. Bitcoin Core [{vstr(first[0])}]({WEB}/{CORE}/releases/tag/{first[1]}) was published "
            f"{first[2]}" + (f", and {vstr(newest[0])} since, on {newest[2]}" if newest != first else "") + ". "
            f"DOG Mode's branch is built on Bitcoin Core {vstr(base)}. How long the {base[0]}.x line keeps getting fixes is "
            "in Bitcoin Core's release lifecycle, https://bitcoincore.org/en/lifecycle/.\n\n"
            f"{pins}\n\n" + closing(r, link=link))})
    return out


def bot_titles(issues):
    """The titles of every issue this workflow opened, open or closed. Anyone can open an issue with any title, so only
    the workflow's own issues can stop it opening one."""
    return {i["title"] for i in issues
            if isinstance(i, dict) and isinstance(i.get("title"), str) and (i.get("user") or {}).get("login") == BOT}


def summary(r):
    yield f"DOG Mode {BRANCH}: tip {r['tip']} ({r['tip_date']}), its version Bitcoin Core {vstr(r['base'])}"
    yield f"pins: Dockerfile {r['docker_pin'][:12]}, build.yml {r['workflow_pin'][:12]}"
    for pin, c in sorted(r["compares"].items()):
        yield f"the tip against the pin {pin[:12]}: {c['status']}, {c['ahead_by']} ahead, {c['behind_by']} behind"
    extra = ", ".join(label(n) for n in new_tags(r)) or "none"
    yield (f"DOG Mode's repository: {len(r['dog_tags'])} tags, {len(r['dog_releases'])} releases; "
           f"not Bitcoin Core's: {extra}")
    line = [f for f in r["core_finals"] if f[0][0] == r["base"][0]]
    newest = max(r["core_finals"])
    yield ((f"Bitcoin Core finals: newest {r['base'][0]}.x {vstr(max(line)[0])} ({max(line)[2]}), " if line else
            f"Bitcoin Core finals: none in {r['base'][0]}.x, ") + f"newest overall {vstr(newest[0])} ({newest[2]})")


# ---- the reads ----

def read_reading(api, root):
    root = Path(root)
    docker_pin, workflow_pin = parse_pins((root / "Dockerfile").read_text(),
                                          (root / ".github" / "workflows" / "build.yml").read_text())
    b = api(f"repos/{DOGMODE}/branches/{BRANCH}")
    try:
        tip, tip_date = b["commit"]["sha"], b["commit"]["commit"]["committer"]["date"]
    except (KeyError, TypeError):
        raise WatchError(f"{DOGMODE} {BRANCH}: the branch came back in an unexpected shape") from None
    need(isinstance(tip, str) and SHA.fullmatch(tip), f"{DOGMODE} {BRANCH}: the tip is not a full commit id")
    need(isinstance(tip_date, str) and DATE.fullmatch(tip_date), f"{DOGMODE} {BRANCH}: the tip's date is not a date")
    compares = {}
    for pin in sorted({docker_pin, workflow_pin}):
        if pin == tip:
            compares[pin] = {"status": "identical", "ahead_by": 0, "behind_by": 0}
            continue
        c = api(f"repos/{DOGMODE}/compare/{pin}...{tip}")
        need(isinstance(c, dict) and c.get("status") in ("ahead", "behind", "identical", "diverged")
             and isinstance(c.get("ahead_by"), int) and isinstance(c.get("behind_by"), int),
             f"{DOGMODE}: the comparison of {pin[:12]} with the tip came back in an unexpected shape")
        compares[pin] = {"status": c["status"], "ahead_by": c["ahead_by"], "behind_by": c["behind_by"]}
    f = api(f"repos/{DOGMODE}/contents/CMakeLists.txt?ref={tip}")
    need(isinstance(f, dict) and f.get("encoding") == "base64" and isinstance(f.get("content"), str),
         f"{DOGMODE}: CMakeLists.txt at the tip came back in an unexpected shape")
    base = parse_base(base64.b64decode(f["content"]).decode("utf-8", "replace"))
    reading = {
        "tip": tip, "tip_date": tip_date, "base": base, "docker_pin": docker_pin, "workflow_pin": workflow_pin,
        "compares": compares,
        "dog_tags": tag_names(api(f"repos/{DOGMODE}/git/matching-refs/tags"), DOGMODE),
        "dog_releases": releases(paged(api, f"repos/{DOGMODE}/releases"), DOGMODE),
        "core_tags": tag_names(api(f"repos/{CORE}/git/matching-refs/tags"), CORE),
    }
    # The newest hundred, newest first: a new release is always among them.
    got = api(f"repos/{CORE}/releases?per_page=100")
    need(isinstance(got, list), f"{CORE}: its releases came back in an unexpected shape")
    reading["core_finals"] = core_finals(releases(got, CORE))
    need(reading["core_finals"], f"{CORE}: no final release among its releases, so the read is wrong")
    need(len(new_tags(reading)) <= MAX_NEW_TAGS,
         f"{len(new_tags(reading))} tags in {DOGMODE} are not Bitcoin Core's; a read of the tags is likely partial")
    return reading


def run(api, repo, root, dry_run, out=print, run_id=None):
    need(REPO_NAME.fullmatch(repo or ""), f"not a repository name: {one_line(repo)}")
    reading = read_reading(api, root)
    lines = list(summary(reading))
    for line in lines:
        out(line)
    found = events(reading, run_link(repo, run_id))
    have = bot_titles(paged(api, f"repos/{repo}/issues?state=all"))
    new = [e for e in found if e["title"] not in have]
    out(f"new things: {len(found)}, of which already opened: {len(found) - len(new)}, to open: {len(new)}")
    need(len(new) <= MAX_NEW_ISSUES, f"{len(new)} issues to open in one run; that is a bad read, not news: "
                                     + "; ".join(e["title"] for e in new))
    if os.environ.get("GITHUB_STEP_SUMMARY") and not dry_run:
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write("\n".join(f"- {line}" for line in lines) + f"\n- to open: {len(new)}\n")
    for e in new:
        if dry_run:
            out(f"WOULD OPEN: {e['title']}")
            out("    " + e["body"].replace("\n", "\n    "))
            continue
        got = api(f"repos/{repo}/issues", method="POST", body={"title": e["title"], "body": e["body"]})
        need(isinstance(got, dict) and isinstance(got.get("number"), int) and got.get("title") == e["title"],
             f"the issue was not created as asked: {e['title']}")
        out(f"opened #{got['number']}: {e['title']}")
    return new


def token_for(dry_run):
    t = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if t or not dry_run:
        return t
    try:   # a dry run on a desk: gh's own login, so no token is typed on a command line
        return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def main(argv=None):
    p = argparse.ArgumentParser(description="Open one issue for each new thing in DOG Mode or Bitcoin Core.")
    p.add_argument("--dry-run", action="store_true", help="print the issues it would open and open nothing")
    a = p.parse_args(argv)
    token = token_for(a.dry_run)
    if not token:
        print("no token: set GH_TOKEN (the workflow passes its own)", file=sys.stderr)
        return 2
    try:
        run(make_api(token), os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO), Path(__file__).resolve().parent.parent,
            a.dry_run, run_id=os.environ.get("GITHUB_RUN_ID"))
    except WatchError as e:
        print(f"::error::{one_line(e, 500)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
