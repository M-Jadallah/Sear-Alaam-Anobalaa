"""Publish verified images, then fast-forward production with an immutable manifest."""
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout.strip()


def github(path, method="GET"):
    req = urllib.request.Request("https://api.github.com/" + path, method=method,
          headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json", "User-Agent": "siyar-release"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return None if response.status == 204 else json.load(response)


def prune_images(images, previous):
    """Delete only this repository's SHA tags, retaining the two deployed releases."""
    deleted = []
    for kind in ("api", "web"):
        package = "sear-alaam-anobalaa-" + kind
        path = "users/M-Jadallah/packages/container/" + package
        details = github(path)
        if details.get("visibility") != "private" or details.get("repository", {}).get("full_name", "").lower() != "m-jadallah/sear-alaam-anobalaa":
            continue
        keep = {images[kind].split("@", 1)[1]}
        if previous.get(kind):
            keep.add(previous[kind].split("@", 1)[1])
        for version in github(path + "/versions?per_page=100"):
            tags = version.get("metadata", {}).get("container", {}).get("tags", [])
            if version["name"] in keep or not tags or not all(re.fullmatch(r"sha-[a-f0-9]{40}", tag) for tag in tags):
                continue
            github(path + "/versions/" + str(int(version["id"])), "DELETE")
            deleted.append({"package": package, "id": version["id"]})
    return deleted


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
    previous_images = {}
    if previous.returncode == 0:
        previous_images = json.loads(run("git", "show", "origin/production:deploy/release.json"))["images"]
    command = ["git", "commit-tree", tree, "-p", sha]
    if previous.returncode == 0:
        command += ["-p", previous.stdout.strip()]
    commit = run(*command, input="Deploy verified release " + sha + "\n")
    run("git", "push", "origin", commit + ":refs/heads/production")
    try:
        release["registry_cleanup"] = prune_images(images, previous_images)
    except Exception as error:
        # Retaining extra images is preferable to failing an already-published
        # release or deleting unrelated versions with uncertain permissions.
        release["registry_cleanup_warning"] = str(error)
    print(json.dumps(release))


if __name__ == "__main__":
    main()
