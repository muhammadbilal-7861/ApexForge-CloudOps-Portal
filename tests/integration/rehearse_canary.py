#!/usr/bin/env python3
"""Opt-in rehearsal in a disposable nested Docker daemon; never uses production AWS.

Run from WSL/Linux: python3 tests/integration/rehearse_canary.py
Requires local docker:29-dind, registry:2 and apexforge-cloudops:cd-check images.
Only the uniquely labeled outer daemon is created/removed on the caller's daemon.
"""

import json
import re
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = "489502663059.dkr.ecr.eu-north-1.amazonaws.com"
REPOSITORY = REGISTRY + "/apexforge-cloudops-portal"
SECRET = "rehearsal-only-signing-key-0123456789abcdef"
LABEL = "org.apexforge.canary-rehearsal"

APP = '''import os, urllib.request
def app(environ, start_response):
    mode = "success"
    if os.environ.get("REHEARSAL_APP") == "new":
        with urllib.request.urlopen("http://127.0.0.1:5999/mode", timeout=2) as response:
            mode = response.read().decode().strip()
    port = environ.get("SERVER_PORT")
    fail = environ.get("PATH_INFO") == "/ready" and (
        (mode == "candidate-failure" and port == "5001") or
        (mode == "production-failure" and port == "5000"))
    start_response("503 Service Unavailable" if fail else "200 OK", [("Content-Type", "text/plain")])
    return [b"rehearsal"]
'''

AWS = '''#!/usr/bin/env python3
import json, subprocess, sys
from pathlib import Path
a = sys.argv[1:]
with open("/rehearsal/aws-calls.jsonl", "a") as log:
    log.write(json.dumps(a) + "\\n")
if a[:2] == ["ecr", "get-login-password"]: print("rehearsal-only-password")
elif a[:2] == ["logs", "describe-log-groups"]: print("/cloudops/app")
elif a[:2] == ["secretsmanager", "get-secret-value"]:
    if "flask-session-key" in " ".join(a): print("SECRET_PLACEHOLDER")
    else: print(json.dumps(dict(host="fixture", username="fixture", password="fixture", dbname="fixture", port=3306)))
elif a[:2] == ["elbv2", "describe-target-health"]:
    mode = Path("/rehearsal/mode").read_text().strip()
    old = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", "cloudops-flask"], capture_output=True, text=True)
    print("healthy" if old.stdout.strip() == "true" or mode != "alb-failure" else "unhealthy")
else:
    print("Unimplemented AWS call in isolated rehearsal", file=sys.stderr)
    sys.exit(2)
'''.replace("SECRET_PLACEHOLDER", SECRET)


def run(command, *, check=True, timeout=180, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, **kwargs)
    if check and result.returncode:
        raise RuntimeError(f"{command}: {result.stdout}\n{result.stderr}")
    return result


def main():
    reports = ROOT / "reports" / "canary-docker-rehearsal"
    reports.mkdir(parents=True, exist_ok=True)
    owner = uuid.uuid4().hex
    outer = None
    outcomes = []
    try:
        for image in ("docker:29-dind", "registry:2", "apexforge-cloudops:cd-check"):
            run(["docker", "image", "inspect", image])
        outer = run([
            "docker", "run", "--detach", "--privileged", "--name", "canary-rehearsal-" + owner,
            "--label", LABEL + "=" + owner, "--add-host", REGISTRY + ":127.0.0.1",
            "--tmpfs", "/var/lib/docker:rw,exec,size=4g",
            "--entrypoint", "dockerd", "docker:29-dind", "--host=unix:///var/run/docker.sock",
            "--storage-driver=vfs", "--insecure-registry=" + REGISTRY,
        ]).stdout.strip()

        def inside(*args, **kwargs):
            return run(["docker", "exec", outer, *args], **kwargs)

        def docker(*args, **kwargs):
            return inside("docker", *args, **kwargs)

        def inspect(ref):
            return json.loads(docker("inspect", ref).stdout)[0]

        def write_remote(path, content):
            run(["docker", "exec", "-i", outer, "sh", "-c", 'cat > "$1"', "writer", path], input=content)

        for _ in range(60):
            if docker("info", check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Disposable Docker daemon did not start")
        inside("apk", "add", "--no-cache", "bash", "python3", "curl", timeout=180)
        print("Disposable daemon ready; loading cached images", flush=True)
        with subprocess.Popen(["docker", "save", "apexforge-cloudops:cd-check", "registry:2"], stdout=subprocess.PIPE) as source:
            loaded = subprocess.run(["docker", "exec", "-i", outer, "docker", "load"], stdin=source.stdout,
                                    capture_output=True, text=True, timeout=240)
            source.stdout.close()
            if loaded.returncode or source.wait(timeout=20):
                raise RuntimeError("Could not load fixture images: " + loaded.stderr)
        inside("mkdir", "-p", "/rehearsal", "/usr/local/sbin")
        write_remote("/rehearsal/mode", "success")
        write_remote("/usr/local/bin/aws", AWS)
        inside("chmod", "755", "/usr/local/bin/aws")
        run(["docker", "exec", "-d", outer, "python3", "-m", "http.server", "5999",
             "--bind", "127.0.0.1", "--directory", "/rehearsal"])
        for name in ("cloudops-deploy.sh", "cloudops-verify.sh"):
            write_remote("/usr/local/sbin/" + name, (ROOT / "deploy" / name).read_text())
            inside("chmod", "755", "/usr/local/sbin/" + name)
        write_remote("/rehearsal/runtime.env", "\n".join([
            "FLASK_ENV=production", "SECRET_KEY=" + SECRET, "SESSION_COOKIE_SECURE=false",
            "USE_AWS_SECRETS=true", "AWS_SECRET_NAME=cloudops/prod/mariadb", "AWS_REGION=eu-north-1",
            "ENABLE_LAB_FAILURE_ENDPOINTS=false", "",
        ]))
        inside("chmod", "600", "/rehearsal/runtime.env")
        write_remote("/rehearsal/wsgi.py", APP)
        write_remote("/rehearsal/Dockerfile", "\n".join([
            "FROM apexforge-cloudops:cd-check", "COPY wsgi.py /srv/cloudops/wsgi.py",
            "ARG REHEARSAL_APP=legacy", "ENV REHEARSAL_APP=$REHEARSAL_APP",
            'HEALTHCHECK --interval=1s --timeout=2s --start-period=1s CMD python -c "import urllib.request; urllib.request.urlopen(\'http://127.0.0.1:5000/health\',timeout=1)"', "",
        ]))
        docker("build", "--provenance=false", "-t", "cloudops-flask:1.1", "/rehearsal", timeout=240)
        docker("build", "--provenance=false", "--build-arg", "REHEARSAL_APP=new", "-t", REPOSITORY + ":fixture", "/rehearsal", timeout=240)
        registry_id = docker("run", "-d", "--name", "rehearsal-registry", "-p", "80:5000", "registry:2").stdout.strip()
        docker("push", REPOSITORY + ":fixture", timeout=180)
        repo_digests = json.loads(docker("image", "inspect", REPOSITORY + ":fixture").stdout)[0]["RepoDigests"]
        image = next(ref for ref in repo_digests if ref.startswith(REPOSITORY + "@sha256:"))
        docker("volume", "create", "rehearsal-data")
        for index, mode in enumerate(("success", "candidate-failure", "production-failure", "alb-failure")):
            print("Real Docker scenario: " + mode, flush=True)
            write_remote("/rehearsal/mode", mode)
            old_id = docker("run", "-d", "--name", "cloudops-flask", "--network", "bridge",
                            "-p", "5000:5000", "-v", "rehearsal-data:/preserved", "cloudops-flask:1.1").stdout.strip()
            for _ in range(30):
                if inside("curl", "-fsS", "http://127.0.0.1:5000/ready", check=False).returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Legacy fixture did not become ready")
            docker("exec", "--user", "0", old_id, "sh", "-c", "echo retained > /preserved/marker")
            before = inspect(old_id)
            short_id = docker("ps", "-q", "--filter", "name=^/cloudops-flask$").stdout.strip()
            assert len(old_id) == 64
            assert short_id == old_id[:12]
            result = run([
                "docker", "exec", "--env", "CLOUDOPS_RUNTIME_ENV=/rehearsal/runtime.env",
                "--env", "CLOUDOPS_CANDIDATE_WAIT_SECONDS=25", "--env", "CLOUDOPS_ALB_WAIT_SECONDS=10",
                outer, "bash", "/usr/local/sbin/cloudops-deploy.sh", image, f"{index + 1:040x}",
                "--target-group-arn", "arn:aws:elasticloadbalancing:eu-north-1:489502663059:targetgroup/tg-cloudops-app/rehearsal",
                "--instance-id", "i-0123456789abcdef0",
            ], check=False, timeout=150)
            (reports / (mode + ".log")).write_text(result.stdout + result.stderr)
            assert (result.returncode == 0) == (mode == "success"), result.stdout + result.stderr
            after = inspect(old_id)
            assert after["Id"] == before["Id"]
            assert after["Config"] == before["Config"]
            assert after["HostConfig"] == before["HostConfig"]
            assert after["Mounts"] == before["Mounts"]
            if mode == "success":
                assert after["State"]["Running"] is False
                assert inspect("cloudops-app")["HostConfig"]["NetworkMode"] == "host"
            else:
                assert after["State"]["Running"] is True
                assert after["Name"] == "/cloudops-flask"
                assert docker("exec", old_id, "cat", "/preserved/marker").stdout.strip() == "retained"
                if mode != "candidate-failure":
                    assert "Previous container restored" in result.stdout
            inside("curl", "-fsS", "http://127.0.0.1:5000/health")
            inside("curl", "-fsS", "http://127.0.0.1:5000/ready")
            outcomes.append(dict(scenario=mode, result="passed", original_id=old_id,
                                 configured_ports=before["HostConfig"]["PortBindings"],
                                 active_ports=before["NetworkSettings"]["Ports"], image=image))
            # This daemon contains only our registry and our disposable scenario containers.
            ids = docker("ps", "-aq", "--no-trunc").stdout.split()
            for container_id in ids:
                if container_id != registry_id:
                    assert re.fullmatch(r"[0-9a-f]{64}", container_id)
                    docker("rm", "-f", container_id)  # No volume removal, including the preserved fixture volume.
        (reports / "results.json").write_text(json.dumps(outcomes, indent=2) + "\n")
        print("All four real Docker scenarios passed; evidence: " + str(reports), flush=True)
    finally:
        if outer:
            document = json.loads(run(["docker", "inspect", outer]).stdout)[0]
            if document["Config"]["Labels"].get(LABEL) != owner:
                raise RuntimeError("Refusing to remove a daemon not owned by this rehearsal")
            logs = run(["docker", "logs", outer], check=False)
            (reports / "daemon.log").write_text(logs.stdout + logs.stderr)
            run(["docker", "rm", "-f", outer])


if __name__ == "__main__":
    main()
