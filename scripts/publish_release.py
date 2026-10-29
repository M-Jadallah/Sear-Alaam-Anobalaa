"""Publish verified images, then fast-forward production with an immutable manifest."""
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request
import urllib.error


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout.strip()


def github(path, method="GET"):
    req = urllib.request.Request("https://api.github.com/" + path, method=method,
          headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json", "User-Agent": "siyar-release"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return None if response.status == 204 else json.load(response)


def require_private_packages(allow_missing=False):
    for kind in ("api", "web"):
        package = "sear-alaam-anobalaa-" + kind
        try:
            details = github("users/M-Jadallah/packages/container/" + package)
        except urllib.error.HTTPError as error:
            if allow_missing and error.code == 404:
                continue
            raise
        if details.get("visibility") != "private":
            raise RuntimeError("Refusing to publish to a non-private package: " + package)


def prune_images(images, previous):
    """Delete only this repository's SHA tags, retaining the two deployed releases."""
    deleted = []
    for kind in ("api", "web"):
        package = "sear-alaam-anobalaa-" + kind
        path = "users/M-Jadallah/packages/container/" + package
        details = github(path)
        if details.get("visibility") != "private" or (details.get("repository") or {}).get("full_name", "").lower() != "m-jadallah/sear-alaam-anobalaa":
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
    run("git", "fetch", "origin", "main")
    if run("git", "rev-parse", "origin/main") != sha or run("git", "rev-parse", "HEAD") != sha:
        raise RuntimeError("A newer source revision exists; release the latest verified main commit.")
    evaluation = json.loads(Path("deploy/test-output/semantic-evaluation.json").read_text())
    if evaluation.get("goals", {}).get("descriptive_hit_at_10_at_least_80_percent") is not True:
        raise RuntimeError("Retrieval release gate failed")
    container_qa = json.loads(Path("deploy/test-output/container-qa.json").read_text())
    if (container_qa["stack_peak_mib"] > 1229 or container_qa["cgroup_peak_sum_mib"] > 1229
            or container_qa["runtime_probe"]["warm_p95_seconds"] > 30
            or container_qa.get("recreate_and_restore") != "passed"):
        raise RuntimeError("Container acceptance gate failed")
    if any(item["events"].get(event, 0) for item in container_qa["cgroups"].values() for event in ("oom", "oom_kill", "oom_group_kill")):
        raise RuntimeError("A process was OOM-killed in the tested container group")
    for kind in ("api", "web"):
        image = "ghcr.io/m-jadallah/sear-alaam-anobalaa-" + kind + ":sha-" + sha
        if container_qa["image_ids"][kind] != run("docker", "image", "inspect", image, "--format", "{{.Id}}"):
            raise RuntimeError("The image differs from the container tested by QA")
    require_private_packages(allow_missing=True)
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
    require_private_packages()
    compose = Path("docker-compose.yml")
    text = compose.read_text(encoding="utf-8")
    for before, after in replacements.items():
        if before not in text:
            raise ValueError("Compose release placeholder missing")
        text = text.replace(before, after)
    compose.write_text(text, encoding="utf-8")
    release = {"source_commit": sha, "images": images, "semantic_evaluation": evaluation,
               "container_qa": container_qa}
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
    Path("deploy/test-output/published-release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(release))


if __name__ == "__main__":
    main()
