"""Linux Docker acceptance. All volumes and networks use a disposable CI suffix."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import urllib.request


def run(*args, check=True, **kwargs):
    kwargs.setdefault("timeout", 600)
    return subprocess.run(args, check=check, text=True, stdout=subprocess.PIPE,
                          stderr=kwargs.pop("stderr", subprocess.PIPE), **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--web", required=True)
    args = parser.parse_args()
    suffix = secrets.token_hex(5)
    folder = Path("deploy/test-output")
    folder.mkdir(parents=True, exist_ok=True)
    network = "siyar-qa-" + suffix
    override = {"services": {key: {"image": args.api} for key in ("init", "api", "backup")},
                "volumes": {"library": {"name": network + "-data"}, "backups": {"name": network + "-backups"}},
                "networks": {"coolify": {"name": network}}}
    override["services"]["web"] = {"image": args.web, "ports": ["127.0.0.1:18080:8080"]}
    override["services"]["api"]["environment"] = {"SIYAR_DISPOSABLE_QA": "1"}
    for service in ("init", "backup"):
        override["services"][service]["environment"] = {"SIYAR_DEPLOYMENT_DIAGNOSTICS": "1"}
    config = folder / "compose-qa.json"
    config.write_text(json.dumps(override), encoding="utf-8")
    os.environ.update(SIYAR_ADMIN_USERNAME="ci-admin", SIYAR_ADMIN_PASSWORD=secrets.token_urlsafe(32))
    command = ["docker", "compose", "-p", network, "-f", "docker-compose.yml", "-f", str(config)]
    def compose(*values, **kw):
        print(json.dumps({"stage": "compose", "command": values[0], "elapsed_seconds": round(time.monotonic() - started, 1)}), flush=True)
        return run(*command, *values, **kw)
    started = time.monotonic()
    run("docker", "network", "create", network)
    peak = 0
    samples = []
    try:
        compose("up", "-d", "--wait", "--wait-timeout", "300")
        print('{"stage":"services_ready"}', flush=True)
        # Check static routes and the actual public POST path through nginx.
        for path in ["/", "/login", "/reader/1", "/topics", "/healthz"]:
            with urllib.request.urlopen("http://127.0.0.1:18080" + path) as response:
                assert response.status == 200
        # API has no public host port and no external network route.
        ids = compose("ps", "-q", "--status", "running").stdout.split()
        with ThreadPoolExecutor(max_workers=1) as executor:
            probe = executor.submit(compose, "exec", "-T", "api", "python", "-m", "backend.production_probe", timeout=1200, stderr=None)
            while not probe.done():
                current = 0
                for identifier in ids:
                    # cgroup usage including page cache is available through inspect+exec
                    # Docker stats is sampled separately to retain readable evidence.
                    output = run("docker", "stats", "--no-stream", "--format", "{{json .}}", identifier).stdout.strip()
                    if not output:
                        continue
                    row = json.loads(output)
                    amount = row["MemUsage"].split(" / ")[0]
                    multipliers = {"GiB": 1024, "MiB": 1, "KiB": 1 / 1024, "B": 1 / 1024**2}
                    for unit, multiplier in multipliers.items():
                        if amount.endswith(unit):
                            current += float(amount[:-len(unit)]) * multiplier
                            break
                peak = max(peak, current)
                samples.append(current)
                time.sleep(.5)
            result = probe.result()
        print('{"stage":"runtime_probe_complete"}', flush=True)
        report = {"runtime_probe": json.loads(result.stdout.strip().splitlines()[-1]), "stack_peak_mib": peak, "sample_count": len(samples)}
        assert peak <= 1229, report
        report["cgroups"] = {}
        for identifier in ids:
            state = json.loads(run("docker", "inspect", identifier).stdout)[0]
            assert not state["State"]["OOMKilled"] and state["RestartCount"] == 0
            service = state["Config"]["Labels"]["com.docker.compose.service"]
            events = dict((key, int(value)) for key, value in (line.split() for line in run("docker", "exec", identifier, "cat", "/sys/fs/cgroup/memory.events").stdout.splitlines()))
            assert {"oom", "oom_kill"}.issubset(events), events
            assert not any(events.get(key, 0) for key in ("oom", "oom_kill", "oom_group_kill")), events
            cgroup_peak = int(run("docker", "exec", identifier, "cat", "/sys/fs/cgroup/memory.peak").stdout.strip())
            report["cgroups"][service] = {"peak_bytes": cgroup_peak, "events": events}
        assert set(report["cgroups"]) == {"api", "web", "backup"}, report["cgroups"]
        report["cgroup_peak_sum_mib"] = sum(item["peak_bytes"] for item in report["cgroups"].values()) / 1024**2
        assert report["cgroup_peak_sum_mib"] <= 1229, report
        print('{"stage":"memory_checks_complete"}', flush=True)
        # A committed review must survive normal reconciliation and recreation.
        before = compose("exec", "-T", "api", "python", "-c", "import sqlite3;print(sqlite3.connect('/state/library.sqlite3').execute('SELECT COUNT(*) FROM review_log').fetchone()[0])").stdout.strip()
        compose("up", "-d", "--wait", "--force-recreate", "--wait-timeout", "300")
        after = compose("exec", "-T", "api", "python", "-c", "import sqlite3;print(sqlite3.connect('/state/library.sqlite3').execute('SELECT COUNT(*) FROM review_log').fetchone()[0])").stdout.strip()
        assert before == after and int(after) > 0
        print('{"stage":"recreate_complete"}', flush=True)
        compose("exec", "-T", "backup", "python", "-m", "backend.deployment", "backup", "--folder", "/backups")
        print('{"stage":"backup_complete"}', flush=True)
        restore = "from pathlib import Path;from backend.deployment import restore,inventory;import os;restore(sorted(Path('/backups').glob('library-daily-*.gz'))[-1],'/backups/restored.sqlite3');assert inventory('/backups/restored.sqlite3')==inventory('/state/library.sqlite3');os.unlink('/backups/restored.sqlite3')"
        compose("exec", "-T", "backup", "python", "-c", restore)
        report["recreate_and_restore"] = "passed"
        report["images"] = {"api": args.api, "web": args.web}
        report["image_ids"] = {kind: run("docker", "image", "inspect", image, "--format", "{{.Id}}").stdout.strip() for kind, image in report["images"].items()}
        report["image_sizes_bytes"] = {kind: int(run("docker", "image", "inspect", image, "--format", "{{.Size}}").stdout.strip()) for kind, image in report["images"].items()}
        report["library_bytes"] = int(compose("exec", "-T", "api", "python", "-c", "from pathlib import Path;print(sum(p.stat().st_size for p in Path('/state').glob('*') if p.is_file()))").stdout.strip())
        report["backup_bytes"] = int(compose("exec", "-T", "backup", "python", "-c", "from pathlib import Path;print(sum(p.stat().st_size for p in Path('/backups').glob('*') if p.is_file()))").stdout.strip())
        (folder / "container-qa.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
    except Exception as error:
        details = str(error)
        if isinstance(error, subprocess.CalledProcessError):
            details += "\n" + (error.stdout or "") + "\n" + (error.stderr or "")
        states = compose("ps", "-a", "-q", check=False).stdout.split()
        for identifier in states:
            state = json.loads(run("docker", "inspect", identifier).stdout)[0]
            details += "\n" + json.dumps({"service": state["Config"]["Labels"]["com.docker.compose.service"], "state": state["State"], "restarts": state["RestartCount"]})
        logs = details + "\n" + compose("logs", "--no-color", "--tail", "60", check=False).stdout
        print(details.replace(os.environ["SIYAR_ADMIN_PASSWORD"], "[REDACTED]"), flush=True)
        (folder / "container-failure.log").write_text(logs.replace(os.environ["SIYAR_ADMIN_PASSWORD"], "[REDACTED]"), encoding="utf-8")
        raise
    finally:
        compose("down", "-v", "--remove-orphans", check=False)
        run("docker", "network", "rm", network, check=False)


if __name__ == "__main__":
    main()
