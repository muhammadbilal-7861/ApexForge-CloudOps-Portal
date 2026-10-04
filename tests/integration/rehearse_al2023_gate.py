#!/usr/bin/env python3
"""Exercise the first-boot firewall gate in disposable AL2023 network namespaces.

No EC2 launch, AWS access, host socket mount or host port publication is involved.
Requires local Docker and network access to the official Amazon Linux repository.
"""
import json
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "public.ecr.aws/amazonlinux/amazonlinux@sha256:12052e9b5d3fd85769abbdd863dd038e1890c9ace31d5fdbe1afa78eda97d061"
LABEL = "org.apexforge.al2023-gate-test"


def run(*args, check=True, timeout=240):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, timeout=timeout)


def main():
    owner = uuid.uuid4().hex
    network = "cloudops-gate-" + owner
    name = network + "-server"
    container = None
    network_created = False
    try:
        run("network", "create", "--label", LABEL + "=" + owner, network)
        network_created = True
        container = run("run", "--detach", "--cap-add", "NET_ADMIN", "--network", network,
                        "--name", name, "--label", LABEL + "=" + owner, IMAGE, "sleep", "infinity").stdout.strip()
        run("exec", container, "dnf", "install", "-y", "nftables", "python3", "curl-minimal")
        run("exec", "--detach", container, "python3", "-m", "http.server", "5000", "--bind", "0.0.0.0")

        def remote():
            return run("run", "--rm", "--network", network, IMAGE, "curl", "--silent", "--retry", "3",
                       "--retry-connrefused", "--max-time", "5", "--output", "/dev/null", "--write-out", "%{http_code}",
                       "http://" + name + ":5000", check=False)

        before = remote()
        assert before.stdout.strip() == "200", before.stderr
        # Execute the exact rules from the reviewed bootstrap, without its other commands.
        template = (ROOT / "deploy/cloudops-user-data.sh.tmpl").read_text()
        import shlex
        rules = [shlex.split(line)[1:] for line in template.splitlines() if line.startswith("nft ")]
        for rule in rules[:-1]:
            run("exec", container, "nft", *rule)
        blocked = remote()
        assert blocked.returncode != 0
        assert blocked.stdout.strip() != "200"
        local = run("exec", container, "curl", "--silent", "--fail", "--output", "/dev/null", "--write-out", "%{http_code}",
                    "http://127.0.0.1:5000")
        assert local.stdout.strip() == "200"
        run("exec", container, "nft", *rules[-1])
        released = remote()
        assert released.stdout.strip() == "200"
        result = {"image": IMAGE, "beforeGate": 200, "remoteWhileGated": "blocked",
                  "loopbackWhileGated": 200, "afterSuccessfulRelease": 200, "productionAwsAccess": False}
        output = ROOT / "reports/al2023-gate-rehearsal.json"
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
    finally:
        if container:
            record = json.loads(run("inspect", container).stdout)[0]
            if record["Config"]["Labels"].get(LABEL) == owner:
                run("rm", "--force", container)
        if network_created:
            record = json.loads(run("network", "inspect", network).stdout)[0]
            if record["Labels"].get(LABEL) == owner:
                run("network", "rm", network)


if __name__ == "__main__":
    main()
