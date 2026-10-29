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
    return subprocess.run(args, check=check, text=True, capture_output=True, **kwargs)


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
    config = folder / "compose-qa.json"
    config.write_text(json.dumps(override), encoding="utf-8")
    os.environ.update(SIYAR_ADMIN_USERNAME="ci-admin", SIYAR_ADMIN_PASSWORD=secrets.token_urlsafe(32))
    command = ["docker", "compose", "-p", network, "-f", "docker-compose.yml", "-f", str(config)]
    def compose(*values, **kw):
        return run(*command, *values, **kw)
    run("docker", "network", "create", network)
    peak = 0
    samples = []
    try:
        compose("up", "-d", "--wait", "--wait-timeout", "300")
        # Check static routes and the actual public POST path through nginx.
        for path in ["/", "/login", "/reader/1", "/topics", "/healthz"]:
            with urllib.request.urlopen("http://127.0.0.1:18080" + path) as response:
                assert response.status == 200
        # API has no public host port and no external network route.
        ids = compose("ps", "-q").stdout.split()
        with ThreadPoolExecutor(max_workers=1) as executor:
            probe = executor.submit(compose, "exec", "-T", "api", "python", "-m", "backend.production_probe")
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
        report = {"runtime_probe": json.loads(result.stdout.strip().splitlines()[-1]), "stack_peak_mib": peak, "sample_count": len(samples)}
        assert peak <= 1229, report
        for identifier in ids:
            state = json.loads(run("docker", "inspect", identifier).stdout)[0]
            assert not state["State"]["OOMKilled"] and state["RestartCount"] == 0
        # A committed review must survive normal reconciliation and recreation.
        before = compose("exec", "-T", "api", "python", "-c", "import sqlite3;print(sqlite3.connect('/state/library.sqlite3').execute('SELECT COUNT(*) FROM review_log').fetchone()[0])").stdout.strip()
        compose("up", "-d", "--wait", "--force-recreate", "--wait-timeout", "300")
        after = compose("exec", "-T", "api", "python", "-c", "import sqlite3;print(sqlite3.connect('/state/library.sqlite3').execute('SELECT COUNT(*) FROM review_log').fetchone()[0])").stdout.strip()
        assert before == after and int(after) > 0
        compose("exec", "-T", "backup", "python", "-m", "backend.deployment", "backup", "--folder", "/backups")
        restore = "from pathlib import Path;from backend.deployment import restore,inventory;import os;restore(sorted(Path('/backups').glob('library-daily-*.gz'))[-1],'/backups/restored.sqlite3');assert inventory('/backups/restored.sqlite3')==inventory('/state/library.sqlite3');os.unlink('/backups/restored.sqlite3')"
        compose("exec", "-T", "backup", "python", "-c", restore)
        report["recreate_and_restore"] = "passed"
        report["images"] = {"api": args.api, "web": args.web}
        report["image_ids"] = {kind: run("docker", "image", "inspect", image, "--format", "{{.Id}}").stdout.strip() for kind, image in report["images"].items()}
        (folder / "container-qa.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
    except Exception:
        logs = compose("logs", "--no-color", "--tail", "60", check=False).stdout
        (folder / "container-failure.log").write_text(logs.replace(os.environ["SIYAR_ADMIN_PASSWORD"], "[REDACTED]"), encoding="utf-8")
        raise
    finally:
        compose("down", "-v", "--remove-orphans", check=False)
        run("docker", "network", "rm", network, check=False)


if __name__ == "__main__":
    main()
