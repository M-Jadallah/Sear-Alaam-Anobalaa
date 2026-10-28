"""Publish verified images, then fast-forward production with an immutable manifest."""
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout.strip()


def main():
    sha = os.environ["RELEASE_SHA"]
    if not re.fullmatch("[a-f0-9]{40}", sha):
        raise ValueError("Invalid source revision")
    replacements = {}
    images = {}
    for kind in ("api", "web"):
        name = f"ghcr.io/m-jadallah/sear-alaam-anobalaa-{kind}"
        image = name + ":sha-" + sha
        run("docker", "push", image)
        digests = json.loads(run("docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"))
        immutable = next(value for value in digests if value.startswith(name + "@sha256:"))
        images[kind] = immutable
        replacements[name + ":unreleased"] = immutable
        # New GHCR packages default to private. Refuse to expose an existing public package.
        package = "sear-alaam-anobalaa-" + kind
        req = urllib.request.Request("https://api.github.com/users/M-Jadallah/packages/container/" + package,
              headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json", "User-Agent": "siyar-release"})
        with urllib.request.urlopen(req, timeout=30) as response:
            assert json.load(response)["visibility"] == "private", "Registry image is not private"
    compose = Path("docker-compose.yml")
    text = compose.read_text(encoding="utf-8")
    for before, after in replacements.items():
        if before not in text:
            raise ValueError("Compose release placeholder missing")
        text = text.replace(before, after)
    compose.write_text(text, encoding="utf-8")
    release = {"source_commit": sha, "images": images, "container_qa": json.loads(Path("deploy/test-output/container-qa.json").read_text())}
    Path("deploy/release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    run("git", "config", "user.name", "siyar-release[bot]")
    run("git", "config", "user.email", "siyar-release@users.noreply.github.com")
    # A two-parent commit keeps production fast-forward-only while deploying
    # exactly this tested main tree. No rebase, reset, or force-push of remote history.
    run("git", "add", "docker-compose.yml", "deploy/release.json")
    tree = run("git", "write-tree")
    run("git", "fetch", "origin")
    previous = subprocess.run(["git", "rev-parse", "--verify", "origin/production"], text=True, capture_output=True)
    command = ["git", "commit-tree", tree, "-p", sha]
    if previous.returncode == 0:
        command += ["-p", previous.stdout.strip()]
    commit = run(*command, input="Deploy verified release " + sha + "\n")
    run("git", "push", "origin", commit + ":refs/heads/production")
    print(json.dumps(release))


if __name__ == "__main__":
    main()
