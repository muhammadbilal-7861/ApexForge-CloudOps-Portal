#!/usr/bin/env python3
"""Exercise the first-boot firewall gate in disposable Ubuntu 24.04 namespaces.

No EC2 launch, AWS access, host socket mount or host port publication is involved.
Requires local Docker and network access to the Ubuntu archive.
"""
import json
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "ubuntu@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55"
LABEL = "org.apexforge.ubuntu24-gate-test"


def run(*args, check=True, timeout=240):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, timeout=timeout)


def main():
    owner = uuid.uuid4().hex
    network = "cloudops-gate-" + owner
    name = network + "-server"
    container = None
    client = None
    network_created = False
    try:
        run("network", "create", "--label", LABEL + "=" + owner, network)
        network_created = True
        container = run("run", "--detach", "--cap-add", "NET_ADMIN", "--network", network,
                        "--name", name, "--label", LABEL + "=" + owner, IMAGE, "sleep", "infinity").stdout.strip()
        run("exec", container, "apt-get", "update")
        run("exec", "--env", "DEBIAN_FRONTEND=noninteractive", container, "apt-get", "install", "-y",
            "--no-install-recommends", "docker.io", "python3", "curl", "ca-certificates", "nftables", "unzip", "snapd")
        # Optional locally reviewed, upstream-signed binaries; never host secrets.
        artifacts = ROOT / ".deploy-work/ubuntu-review"
        binaries_checked = False
        if (artifacts / "awscliv2.zip").exists() and (artifacts / "cloudwatch-versioned.deb").exists():
            run("cp", str(artifacts / "awscliv2.zip"), container + ":/tmp/awscliv2.zip")
            run("cp", str(artifacts / "cloudwatch-versioned.deb"), container + ":/tmp/cloudwatch.deb")
            run("exec", container, "sh", "-c",
                "printf '%s\n' '850ba65f1342a1f725f4868de3c4621ea729af31dac96e54b574cbb0ef309029  /tmp/awscliv2.zip' "
                "'f25c81f42627ac481b51215e8e6f989208ab266f8b224ffd66a208061e790f1c  /tmp/cloudwatch.deb' | sha256sum -c")
            run("exec", container, "unzip", "-q", "/tmp/awscliv2.zip", "-d", "/tmp")
            run("exec", container, "/tmp/aws/install", "--install-dir", "/usr/local/aws-cli", "--bin-dir", "/usr/local/bin")
            version = run("exec", container, "aws", "--version")
            assert "aws-cli/2.37.5 " in version.stdout
            run("exec", container, "dpkg", "-i", "/tmp/cloudwatch.deb")
            run("exec", container, "test", "-x", "/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl")
            binaries_checked = True
        client = run("run", "--detach", "--network", network, "--label", LABEL + "=" + owner,
                     IMAGE, "sleep", "infinity").stdout.strip()
        run("exec", client, "apt-get", "update")
        run("exec", client, "apt-get", "install", "-y", "--no-install-recommends", "curl")
        run("exec", "--detach", container, "python3", "-m", "http.server", "5000", "--bind", "0.0.0.0")

        def remote():
            return run("exec", client, "curl", "--silent", "--retry", "3",
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
                  "loopbackWhileGated": 200, "afterSuccessfulRelease": 200, "productionAwsAccess": False,
                  "ubuntuArchivePackagesInstalled": True, "reviewedAwsBinariesInstalled": binaries_checked,
                  "systemdAndSnapServiceAcceptance": "requires later EC2 rollout"}
        output = ROOT / "reports/ubuntu24-gate-rehearsal.json"
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
    finally:
        for target in (container, client):
            if not target:
                continue
            record = json.loads(run("inspect", target).stdout)[0]
            if record["Config"]["Labels"].get(LABEL) == owner:
                run("rm", "--force", target)
        if network_created:
            record = json.loads(run("network", "inspect", network).stdout)[0]
            if record["Labels"].get(LABEL) == owner:
                run("network", "rm", network)


if __name__ == "__main__":
    main()
