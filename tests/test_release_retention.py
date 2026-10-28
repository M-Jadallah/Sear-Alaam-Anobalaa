from scripts import publish_release as release


def test_registry_cleanup_keeps_current_previous_and_nonrelease_tags(monkeypatch):
    deleted = []
    def github(path, method="GET"):
        if method == "DELETE":
            deleted.append(path)
            return None
        if "/versions?" in path:
            return [{"id": i, "name": digest, "metadata": {"container": {"tags": tags}}} for i, digest, tags in [
                (1, "sha256:current", ["sha-" + "1" * 40]),
                (2, "sha256:previous", ["sha-" + "2" * 40]),
                (3, "sha256:older", ["sha-" + "3" * 40]),
                (4, "sha256:operator", ["manual-retain"]),
                (5, "sha256:untagged", []),
            ]]
        return {"visibility": "private", "repository": {"full_name": "M-Jadallah/Sear-Alaam-Anobalaa"}}
    monkeypatch.setattr(release, "github", github)
    images = {kind: "ghcr.io/example/" + kind + "@sha256:current" for kind in ("api", "web")}
    previous = {kind: "ghcr.io/example/" + kind + "@sha256:previous" for kind in ("api", "web")}
    assert len(release.prune_images(images, previous)) == 2
    assert all(path.endswith("/versions/3") for path in deleted)


def test_registry_cleanup_refuses_unverified_repository_ownership(monkeypatch):
    calls = []
    def github(path, method="GET"):
        calls.append((path, method))
        return {"visibility": "private", "repository": {"full_name": "someone/another-project"}}
    monkeypatch.setattr(release, "github", github)
    assert release.prune_images({"api": "api@sha256:current", "web": "web@sha256:current"}, {}) == []
    assert all(method == "GET" for _, method in calls)
