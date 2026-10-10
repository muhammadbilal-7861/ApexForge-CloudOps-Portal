# 10 — Jenkins, SonarQube and security gates

[Master guide](../MASTER-GUIDE.md).

## Architecture and purpose

![Pipeline](../diagrams/04-pipeline.png)
[Editable SVG](../diagrams/04-pipeline.svg). Jenkins runs in Docker as container **jenkins**, stores home/workspace in a named volume and uses the host Docker socket. Scanner containers use `--volumes-from jenkins`; `$WORKSPACE` is not a host bind-mount path. Sonar Scanner uses host networking and persistent `.scannerwork` metadata.

## Prerequisites

Owned private tools EC2 with Docker, Git, Bash, Python3 and sufficient RAM/disk. SSM UI tunnel from Lab05. Use reviewed digest-pinned Jenkins/Sonar/Postgres images and record versions. Read [Jenkins security guidance](https://www.jenkins.io/doc/book/security/) before granting any user build/configure permissions.

## Installation and configuration

Tools SSM shell: obtain official image digests, review support/security notices, then build a controller with required CLI programs. Save this **host provisioning** Dockerfile outside the app repository:

```dockerfile
ARG JENKINS_BASE
FROM ${JENKINS_BASE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    docker.io python3 git bash ca-certificates && rm -rf /var/lib/apt/lists/*
USER jenkins
```

Use the official `jenkins/jenkins:lts-jdk21` release as the reviewed starting point; resolve its digest with `docker pull` / `docker image inspect --format '{{json .RepoDigests}}'`. Supply `JENKINS_BASE=jenkins/jenkins@sha256:<reviewed digest>`. Check Docker client compatibility against the host daemon. No host credentials are copied into the image.

```bash
docker build --build-arg "JENKINS_BASE=$JENKINS_BASE" -t apexforge-jenkins:reviewed /path/to/controller-build
docker volume create jenkins_home
SOCKET_GID=$(stat -c '%g' /var/run/docker.sock)
docker run -d --name jenkins --restart unless-stopped --network host \
  --group-add "$SOCKET_GID" --env JENKINS_OPTS=--httpListenAddress=127.0.0.1 \
  --mount type=volume,source=jenkins_home,target=/var/jenkins_home \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
  apexforge-jenkins:reviewed
docker exec jenkins docker version
```

The supplemental socket GID grants the non-root Jenkins user daemon access; do not chmod777/666 the socket. View the initial admin password privately with `docker exec jenkins cat /var/jenkins_home/secrets/initialAdminPassword`, open the SSM-forwarded localhost8080 UI and complete setup. Never publish that output.

Install plugins: Pipeline/Declarative, Git, Credentials Binding, SonarQube Scanner, Pipeline Utility Steps, JUnit, and Timestamper. Configure the trusted executor labels `linux docker`; this reference uses the controller volume, so run only owner-reviewed code. Restrict `Job/Configure`, credentials, script approvals and deployment approvers. A separate hardened agent design requires reviewing the workspace-volume contract first.

Sonar needs a persistent supported PostgreSQL database and volumes; do not use its embedded DB for sustained operation. On tools host, prepare private0600 env files for Postgres and Sonar with a unique password (private editor; never paste values in shell history). Postgres env: `POSTGRES_USER=sonar`, `POSTGRES_DB=sonar`, `POSTGRES_PASSWORD=<private value>`. Sonar env: `SONAR_JDBC_URL=jdbc:postgresql://127.0.0.1:5432/sonar`, `SONAR_JDBC_USERNAME=sonar`, `SONAR_JDBC_PASSWORD=<same private value>`.

```bash
# Set these to official image references pinned by reviewed digest first.
docker volume create sonar_db
docker volume create sonar_data
docker volume create sonar_extensions
docker volume create sonar_logs
# Apply current Sonar host requirements; validate limits as documented for your release.
sudo sysctl -w vm.max_map_count=524288
docker run -d --name sonar-postgres --restart unless-stopped \
  -p 127.0.0.1:5432:5432 --env-file /etc/cloudops/sonar-postgres.env \
  --mount type=volume,source=sonar_db,target=/var/lib/postgresql/data "$POSTGRES_IMAGE"
docker run -d --name sonarqube --restart unless-stopped --network host \
  --ulimit nofile=131072:131072 --ulimit nproc=8192:8192 \
  --env-file /etc/cloudops/sonar.env \
  --mount type=volume,source=sonar_data,target=/opt/sonarqube/data \
  --mount type=volume,source=sonar_extensions,target=/opt/sonarqube/extensions \
  --mount type=volume,source=sonar_logs,target=/opt/sonarqube/logs "$SONAR_IMAGE"
curl --fail http://127.0.0.1:9000/api/system/status
```

Use the Postgres major supported by your reviewed Sonar release; the `/var/lib/postgresql/data` mount above is for releases that use that directory (e.g.16/17), not a different major's layout. Persist sysctl after review in `/etc/sysctl.d/99-sonarqube.conf`. Access Sonar through a separate SSM9000 tunnel, change default admin password, create project key **ApexForge-CloudOps-Portal**, keep its project name from `sonar-project.properties`, and generate a project analysis token privately.

Jenkins → Credentials: store that token as Secret text ID **sonarqube-token**. Jenkins → System → SonarQube servers: name **sonarqube**, URL `http://127.0.0.1:9000`, credential that ID. Configure Sonar project webhook **http://127.0.0.1:8080/sonarqube-webhook/** because both containers use host networking. The trailing slash matters. For webhook authentication configure a secret through the Sonar/Jenkins UI and the documented Jenkins plugin integration; never commit it. Keep Jenkins `waitForQualityGate abortPipeline: true` ([Sonar pipeline pause](https://docs.sonarsource.com/sonarqube-server/2025.5/analyzing-source-code/ci-integration/jenkins-integration/pipeline-pause)).

Create a normal Pipeline script from SCM job pointing at **your fork**, `*/main`, script path `Jenkinsfile`. Public checkout requires no GitHub token. For private forks store credential in Jenkins only. Set private inventory/approvers as in Lab09. Run with `AWS_OPERATIONS=false`, `DEPLOY_TARGET=none`, Trivy `HIGH,CRITICAL`, exit1 first.

## Expected output and gates

Checkout prints main/SHA. Gitleaks fails on secrets. Hashed wheel install/pip check precedes pytest. Sonar analysis writes `$WORKSPACE/.scannerwork/report-task.txt`; Jenkins waits for its webhook quality gate. Trivy produces full fixed/unfixed HIGH/CRITICAL table/JSON, then gates fixable vulnerabilities (`--ignore-unfixed`) with exit1. Misconfiguration scanning uses its separate blocking gate. Docker build/image scan precede report archive. Unfixed findings require documented risk review; no blanket CVE suppressions.

## Validation

Verify JUnit and all security artifacts. Test a deliberately failing change only in a disposable trusted lab branch; confirm gates stop AWS operations. Confirm unauthorized approval fails. A green local Groovy parse is not a live Jenkins plugin execution test.

## Troubleshooting and root causes

Quality gate missing task: scanner working directory is ephemeral; retain the workspace metadata. Scanner access denied: use Jenkins UID/GID and directory0700. Trivy root cache must stay in `apexforge-trivy-cache` volume, not workspace. Sonar unreachable: scanner needs host network. Pending quality gate: webhook route, trailing slash or secret mismatch. Never replace the webhook with a weakened gate.

## Security considerations

Docker socket and `--volumes-from jenkins` can expose controller state. This is a trusted learning topology, not an untrusted-fork build service. Do not grant public users executor/configuration rights. Restrict artifact readers and keep operational metadata private.

## Cleanup

Back up owned Jenkins/Sonar/Postgres volumes before retiring tools. Stop/remove only recorded lab containers. Do not delete shared volumes, tokens or jobs as routine cleanup; revoke obsolete tokens through the owner-controlled UI.

## Interview questions with answers

**Why report and gate separately?** Complete evidence includes unresolved risk; the blocking rule enforces available fixes without hiding unfixed findings.
**Why named-volume sharing?** The daemon interprets bind paths on the host, while Jenkins workspace paths live inside its named volume.
