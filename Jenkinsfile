pipeline {
    agent { label 'linux && docker' }

    options {
        skipDefaultCheckout(true)
        timestamps()
        disableConcurrentBuilds()
        timeout(time: 120, unit: 'MINUTES')
    }

    parameters {
        choice(
            name: 'TRIVY_SEVERITY',
            choices: ['HIGH,CRITICAL', 'CRITICAL', 'MEDIUM,HIGH,CRITICAL'],
            description: 'Vulnerability severities to report and gate on.'
        )
        choice(
            name: 'TRIVY_EXIT_CODE',
            choices: ['1', '0'],
            description: '1 fails on matching Trivy findings; 0 reports findings without failing.'
        )
        choice(
            name: 'DEPLOY_TARGET',
            choices: ['none', 'canary', 'asg'],
            description: 'Deployment is opt-in. Canary targets the prepared existing EC2 instance; ASG performs a controlled launch-template rollout.'
        )
        booleanParam(
            name: 'ASG_AMI_REVIEWED',
            defaultValue: false,
            description: 'For ASG only: confirm the pinned official Ubuntu 24.04 LTS image provenance and generated launch-template preview have been reviewed.'
        )
    }

    environment {
        PYTHON_IMAGE = 'python:3.12-slim@sha256:6b1f85a08c199d29d5b6d71ab9c27bd5b3b393492e01216a15758ff69c4be8b8'
        GITLEAKS_IMAGE = 'ghcr.io/gitleaks/gitleaks:v8.29.1'
        TRIVY_IMAGE = 'ghcr.io/aquasecurity/trivy:0.74.0'
        TRIVY_CACHE_VOLUME = 'apexforge-trivy-cache'
        SONAR_SCANNER_IMAGE = 'sonarsource/sonar-scanner-cli:12.1.0.3233_8.0.1'
        SONARQUBE_SERVER = 'sonarqube'
        SONAR_TOKEN_CREDENTIAL_ID = 'sonarqube-token'
        APP_IMAGE_NAME = 'apexforge-cloudops'
        AWS_CLI_IMAGE = 'public.ecr.aws/aws-cli/aws-cli:2.37.5'
        AWS_REGION = 'eu-north-1'
        AWS_ACCOUNT_ID = '489502663059'
        AWS_EXPECTED_ROLE = 'DevSecOpsToolsRole'
        ECR_REPOSITORY = 'apexforge-cloudops-portal'
        ECR_REGISTRY = '489502663059.dkr.ecr.eu-north-1.amazonaws.com'
        ECR_URI = '489502663059.dkr.ecr.eu-north-1.amazonaws.com/apexforge-cloudops-portal'
        TRIVY_SEVERITY = "${params.TRIVY_SEVERITY}"
        TRIVY_EXIT_CODE = "${params.TRIVY_EXIT_CODE}"
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Environment / build information') {
            steps {
                script {
                    env.GIT_COMMIT_SHORT = sh(
                        script: 'git rev-parse --short=12 HEAD',
                        returnStdout: true
                    ).trim()
                    env.GIT_COMMIT_FULL = sh(
                        script: 'git rev-parse HEAD',
                        returnStdout: true
                    ).trim()
                    env.APP_IMAGE_REF = "${env.APP_IMAGE_NAME}:${env.BUILD_NUMBER}-${env.GIT_COMMIT_SHORT}"

                    def scmBranch = ""
                    if (env.GIT_BRANCH != null) {
                        scmBranch = env.GIT_BRANCH.trim()
                    }
                    scmBranch = scmBranch.replaceFirst('^refs/remotes/', "").replaceFirst('^remotes/', "").replaceFirst('^refs/heads/', "").replaceFirst('^origin/', "")

                    if (!scmBranch || scmBranch == 'HEAD') {
                        def containingBranches = sh(
                            script: "git for-each-ref --contains HEAD --format='%(refname:short)' refs/remotes/origin | sed 's#^origin/##' | grep -v '^HEAD\$' | sort -u || true",
                            returnStdout: true
                        ).trim().readLines().findAll { it }
                        def exactBranches = sh(
                            script: "git for-each-ref --points-at HEAD --format='%(refname:short)' refs/remotes/origin | sed 's#^origin/##' | grep -v '^HEAD\$' | sort -u || true",
                            returnStdout: true
                        ).trim().readLines().findAll { it }

                        if (exactBranches.size() == 1) {
                            scmBranch = exactBranches[0]
                        } else if (containingBranches.size() == 1) {
                            scmBranch = containingBranches[0]
                        } else {
                            scmBranch = 'unknown'
                        }
                    }
                    if (!scmBranch) {
                        scmBranch = 'unknown'
                    }
                    env.SCM_BRANCH = scmBranch
                }
                sh '''#!/bin/sh
                    set -eu
                    mkdir -p reports .pip-cache
                    docker volume create "$TRIVY_CACHE_VOLUME" >/dev/null
                    printf '%s\n' '--- ApexForge CloudOps CI build ---'
                    printf 'Job: %s\nBuild: %s\nBranch: %s\nCommit: %s\nAgent: %s\n' \
                        "$JOB_NAME" "$BUILD_NUMBER" "$SCM_BRANCH" "$GIT_COMMIT_SHORT" "$(uname -a)"
                    printf 'Image: %s\nTrivy severities: %s (exit code %s)\n' \
                        "$APP_IMAGE_REF" "$TRIVY_SEVERITY" "$TRIVY_EXIT_CODE"
                    printf 'Health endpoints: /health (liveness), /ready (database readiness), /metrics\n'
                '''
            }
        }

        stage('Secret scanning - Gitleaks') {
            steps {
                sh '''#!/bin/sh
                    set -eu
                    docker run --rm \
                        --volumes-from jenkins \
                        --workdir "$WORKSPACE" \
                        "$GITLEAKS_IMAGE" git \
                        --no-banner \
                        --redact \
                        --report-format sarif \
                        --report-path "$WORKSPACE/reports/gitleaks.sarif" \
                        --exit-code 1 \
                        "$WORKSPACE"
                '''
            }
        }

        stage('Python dependency installation') {
            steps {
                sh '''#!/bin/sh
                    set -eu
                    docker run --rm \
                        --volumes-from jenkins \
                        --workdir "$WORKSPACE" \
                        --env WORKSPACE="$WORKSPACE" \
                        --env PIP_CACHE_DIR="$WORKSPACE/.pip-cache" \
                        "$PYTHON_IMAGE" \
                        sh -ec 'python -m venv --clear "$WORKSPACE/.ci-venv" && "$WORKSPACE/.ci-venv/bin/pip" install --disable-pip-version-check --only-binary=:all: --require-hashes -r requirements.txt && "$WORKSPACE/.ci-venv/bin/pip" check'
                '''
            }
        }

        stage('Unit tests - pytest') {
            steps {
                sh '''#!/bin/sh
                    set -eu
                    mkdir -p reports
                    docker run --rm \
                        --volumes-from jenkins \
                        --workdir "$WORKSPACE" \
                        "$PYTHON_IMAGE" \
                        "$WORKSPACE/.ci-venv/bin/python" -m pytest -q --junitxml=reports/pytest.xml
                '''
            }
        }

        stage('SonarQube SAST / quality analysis') {
            steps {
                withSonarQubeEnv(SONARQUBE_SERVER) {
                    withCredentials([string(
                        credentialsId: SONAR_TOKEN_CREDENTIAL_ID,
                        variable: 'SONAR_TOKEN'
                    )]) {
                        sh '''#!/bin/sh
                            set -eu
                            mkdir -p "$WORKSPACE/.scannerwork"
                            chmod 700 "$WORKSPACE/.scannerwork"
                            docker run --rm \
                                --network host \
                                --volumes-from jenkins \
                                --user "$(id -u):$(id -g)" \
                                --workdir "$WORKSPACE" \
                                --env SONAR_HOST_URL \
                                --env SONAR_TOKEN \
                                --env SONAR_USER_HOME=/tmp/.sonar \
                                "$SONAR_SCANNER_IMAGE" \
                                "-Dsonar.working.directory=$WORKSPACE/.scannerwork"
                        '''
                    }
                }
            }
        }

        stage('SonarQube Quality Gate') {
            steps {
                timeout(time: 15, unit: 'MINUTES') {
                    waitForQualityGate abortPipeline: true
                }
            }
        }

        stage('Trivy filesystem / SCA scan') {
            steps {
                sh '''#!/bin/sh
                    set -eu
                    # Reporting scans include fixed and unfixed vulnerabilities; they never gate on findings.
                    docker run --rm \
                        --volumes-from jenkins \
                        --mount "type=volume,source=$TRIVY_CACHE_VOLUME,target=/trivy-cache" \
                        --workdir "$WORKSPACE" \
                        --env TRIVY_CACHE_DIR=/trivy-cache \
                        "$TRIVY_IMAGE" fs \
                        --scanners vuln,misconfig \
                        --skip-dirs .git,.ci-venv,.pip-cache,.sonar,.scannerwork,reports,.pytest_cache,__pycache__,.venv,venv \
                        --severity "$TRIVY_SEVERITY" \
                        --exit-code 0 \
                        --format table \
                        .
                    docker run --rm \
                        --volumes-from jenkins \
                        --mount "type=volume,source=$TRIVY_CACHE_VOLUME,target=/trivy-cache" \
                        --workdir "$WORKSPACE" \
                        --env TRIVY_CACHE_DIR=/trivy-cache \
                        "$TRIVY_IMAGE" fs \
                        --scanners vuln,misconfig \
                        --skip-dirs .git,.ci-venv,.pip-cache,.sonar,.scannerwork,reports,.pytest_cache,__pycache__,.venv,venv \
                        --severity "$TRIVY_SEVERITY" \
                        --exit-code 0 \
                        --format json \
                        --output "$WORKSPACE/reports/trivy-filesystem.json" \
                        .

                    # Enforce only fixable HIGH/CRITICAL package vulnerabilities; unfixed findings remain in the reports and require documented risk review.
                    docker run --rm \
                        --volumes-from jenkins \
                        --mount "type=volume,source=$TRIVY_CACHE_VOLUME,target=/trivy-cache" \
                        --workdir "$WORKSPACE" \
                        --env TRIVY_CACHE_DIR=/trivy-cache \
                        "$TRIVY_IMAGE" fs \
                        --scanners vuln \
                        --skip-dirs .git,.ci-venv,.pip-cache,.sonar,.scannerwork,reports,.pytest_cache,__pycache__,.venv,venv \
                        --ignore-unfixed \
                        --severity "$TRIVY_SEVERITY" \
                        --exit-code "$TRIVY_EXIT_CODE" \
                        --format table \
                        .

                    # Misconfiguration findings are gated separately; ignore-unfixed does not apply to IaC/config checks.
                    docker run --rm \
                        --volumes-from jenkins \
                        --mount "type=volume,source=$TRIVY_CACHE_VOLUME,target=/trivy-cache" \
                        --workdir "$WORKSPACE" \
                        --env TRIVY_CACHE_DIR=/trivy-cache \
                        "$TRIVY_IMAGE" fs \
                        --scanners misconfig \
                        --skip-dirs .git,.ci-venv,.pip-cache,.sonar,.scannerwork,reports,.pytest_cache,__pycache__,.venv,venv \
                        --severity "$TRIVY_SEVERITY" \
                        --exit-code "$TRIVY_EXIT_CODE" \
                        --format table \
                        .
                '''
            }
        }

        stage('Terraform / CloudFormation validation extension point') {
            steps {
                script {
                    def terraformFiles = sh(
                        script: "git ls-files '*.tf' '*.tf.json' '*.tfvars' | grep -q .",
                        returnStatus: true
                    ) == 0
                    def cloudFormationFiles = sh(
                        script: "git grep -lE 'AWSTemplateFormatVersion|AWS::[[:alnum:]]+::' -- '*.yaml' '*.yml' '*.json' '*.template' >/dev/null",
                        returnStatus: true
                    ) == 0

                    if (terraformFiles || cloudFormationFiles) {
                        echo 'Infrastructure templates found; running Trivy config validation.'
                        sh '''#!/bin/sh
                            set -eu
                            docker run --rm \
                                --volumes-from jenkins \
                                --mount "type=volume,source=$TRIVY_CACHE_VOLUME,target=/trivy-cache" \
                                --workdir "$WORKSPACE" \
                                --env TRIVY_CACHE_DIR=/trivy-cache \
                                "$TRIVY_IMAGE" config \
                                --scanners misconfig \
                                --severity "$TRIVY_SEVERITY" \
                                --exit-code "$TRIVY_EXIT_CODE" \
                                --format json \
                                --output "$WORKSPACE/reports/trivy-infrastructure.json" \
                                .
                        '''
                    } else {
                        echo 'No Terraform or CloudFormation templates are present. IaC validation is an extension point; no infrastructure was created or changed.'
                    }
                }
            }
        }

        stage('Docker image build') {
            steps {
                sh '''#!/bin/sh
                    set -eu
                    bash deploy/build-image.sh "$APP_IMAGE_REF"
                '''
            }
        }

        stage('Trivy Docker image vulnerability scan') {
            steps {
                sh '''#!/bin/sh
                    set -eu
                    # Complete reporting includes fixable and unfixed HIGH/CRITICAL findings.
                    docker run --rm \
                        --volumes-from jenkins \
                        --mount "type=volume,source=$TRIVY_CACHE_VOLUME,target=/trivy-cache" \
                        --workdir "$WORKSPACE" \
                        --env TRIVY_CACHE_DIR=/trivy-cache \
                        "$TRIVY_IMAGE" image \
                        --image-src docker \
                        --scanners vuln \
                        --severity "$TRIVY_SEVERITY" \
                        --exit-code 0 \
                        --format table \
                        "$APP_IMAGE_REF"
                    docker run --rm \
                        --volumes-from jenkins \
                        --mount "type=volume,source=$TRIVY_CACHE_VOLUME,target=/trivy-cache" \
                        --workdir "$WORKSPACE" \
                        --env TRIVY_CACHE_DIR=/trivy-cache \
                        "$TRIVY_IMAGE" image \
                        --image-src docker \
                        --scanners vuln \
                        --severity "$TRIVY_SEVERITY" \
                        --exit-code 0 \
                        --format json \
                        --output "$WORKSPACE/reports/trivy-image.json" \
                        "$APP_IMAGE_REF"

                    # Release gate: fail for fixable HIGH/CRITICAL vulnerabilities. Unfixed findings stay in the complete reports and require documented risk review, not silent suppression.
                    docker run --rm \
                        --volumes-from jenkins \
                        --mount "type=volume,source=$TRIVY_CACHE_VOLUME,target=/trivy-cache" \
                        --workdir "$WORKSPACE" \
                        --env TRIVY_CACHE_DIR=/trivy-cache \
                        "$TRIVY_IMAGE" image \
                        --image-src docker \
                        --scanners vuln \
                        --severity "$TRIVY_SEVERITY" \
                        --ignore-unfixed \
                        --exit-code "$TRIVY_EXIT_CODE" \
                        --format table \
                        "$APP_IMAGE_REF"
                '''
            }
        }

        stage('Security report manifest') {
            steps {
                sh '''#!/bin/sh
                    set -eu
                    printf '%s\n' 'Security reports generated in reports/:'
                    find reports -maxdepth 1 -type f -printf '%f (%s bytes)\n' | sort
                '''
            }
        }

        stage('Push image to Amazon ECR') {
            when {
                expression { env.SCM_BRANCH == 'main' }
            }
            steps {
                sh '''#!/bin/bash
                    set -euo pipefail
                    set +x
                    python3 deploy/publish-ecr-image.py
                '''
            }
        }

        stage('Authorize opt-in deployment') {
            when {
                expression { params.DEPLOY_TARGET in ['canary', 'asg'] }
            }
            steps {
                script {
                    sh '''#!/bin/bash
                        set -euo pipefail
                        set +x
                        [[ "$SCM_BRANCH" == main ]] || { echo 'Deployment is restricted to main.' >&2; exit 1; }
                        [[ "$TRIVY_SEVERITY" == 'HIGH,CRITICAL' && "$TRIVY_EXIT_CODE" == '1' ]] || {
                            echo 'Deployment requires HIGH,CRITICAL reporting and enabled Trivy blocking gates.' >&2; exit 1;
                        }
                        bash deploy/assert-deploy-context.sh
                    '''
                    if (params.DEPLOY_TARGET == 'asg' && !params.ASG_AMI_REVIEWED) {
                        error('ASG deployment requires ASG_AMI_REVIEWED=true after official-image provenance and bootstrap preview review.')
                    }
                    def digest = sh(
                        script: '''#!/bin/bash
                            set -euo pipefail
                            docker run --rm --network host --volumes-from jenkins \\
                                --env AWS_REGION="$AWS_REGION" --env AWS_DEFAULT_REGION="$AWS_REGION" \\
                                "$AWS_CLI_IMAGE" ecr describe-repositories \\
                                --repository-names "$ECR_REPOSITORY" \\
                                --query 'repositories[0].imageTagMutability' --output text | grep -Fx IMMUTABLE >/dev/null
                            docker run --rm --network host --volumes-from jenkins \\
                                --env AWS_REGION="$AWS_REGION" --env AWS_DEFAULT_REGION="$AWS_REGION" \\
                                "$AWS_CLI_IMAGE" ecr describe-images \\
                                --repository-name "$ECR_REPOSITORY" \\
                                --image-ids "imageTag=$GIT_COMMIT_SHORT" \\
                                --query 'imageDetails[0].imageDigest' --output text
                        ''',
                        returnStdout: true
                    ).trim()
                    if (!digest.matches('sha256:[0-9a-f]{64}')) {
                        error('ECR did not return a valid immutable image digest.')
                    }
                    env.ECR_IMAGE_DIGEST = digest
                    env.ECR_DEPLOY_IMAGE = "${env.ECR_URI}@${digest}"
                    sh '''#!/bin/bash
                        set -euo pipefail
                        mkdir -p reports
                        printf '{"commit":"%s","image":"%s","target":"%s"}\\n' \\
                            "$GIT_COMMIT_FULL" "$ECR_DEPLOY_IMAGE" "$DEPLOY_TARGET" > reports/deployment-release.json
                    '''
                    withEnv(["ECR_DEPLOY_IMAGE=${env.ECR_DEPLOY_IMAGE ?: ''}", "ECR_IMAGE_DIGEST=${env.ECR_IMAGE_DIGEST ?: ''}"]) {
                        sh '''#!/bin/bash
                            set -euo pipefail
                            bash deploy/collect-cloudops-preflight.sh "$DEPLOY_TARGET"
                        '''
                    }
                    if (params.DEPLOY_TARGET == 'asg') {
                        archiveArtifacts artifacts: 'reports/asg-launch-template-preview.json', fingerprint: true
                        archiveArtifacts artifacts: 'reports/asg-launch-permissions.json', fingerprint: true
                        archiveArtifacts artifacts: 'reports/asg-launch-candidate.json', fingerprint: true
                        env.ASG_VALIDATED_VERSION = sh(
                            script: '''#!/bin/bash
                                set -euo pipefail
                                docker run --rm --volumes-from jenkins --workdir "$WORKSPACE" "$PYTHON_IMAGE" \\
                                    python -c 'import json; print(json.load(open("reports/asg-launch-candidate.json"))["candidateVersion"])'
                            ''', returnStdout: true
                        ).trim()
                        if (!env.ASG_VALIDATED_VERSION.matches('[0-9]+')) {
                            error('Missing validated ASG candidate version.')
                        }
                        echo "Review the launch-template preview before approval: ${env.BUILD_URL}artifact/reports/asg-launch-template-preview.json"
                    }
                }
            }
        }

        stage('Manual deployment approval') {
            when {
                expression { params.DEPLOY_TARGET in ['canary', 'asg'] }
            }
            steps {
                script {
                    if (!env.CLOUDOPS_DEPLOY_APPROVERS || !env.CLOUDOPS_DEPLOY_APPROVERS.trim()) {
                        error('Set the Jenkins global environment variable CLOUDOPS_DEPLOY_APPROVERS to the approved user IDs before enabling deployment.')
                    }
                }
                timeout(time: 15, unit: 'MINUTES') {
                    input(
                        message: "Deploy ${env.GIT_COMMIT_SHORT} to ${params.DEPLOY_TARGET}? Architecture and security preflight passed." +
                            (params.DEPLOY_TARGET == 'asg' ? " Candidate version ${env.ASG_VALIDATED_VERSION}, image ${env.ECR_DEPLOY_IMAGE}. Review ${env.BUILD_URL}artifact/reports/asg-launch-template-preview.json and asg-launch-candidate.json before approving." : ''),
                        ok: 'Approve deployment',
                        submitter: env.CLOUDOPS_DEPLOY_APPROVERS,
                        submitterParameter: 'DEPLOY_APPROVED_BY'
                    )
                }
            }
        }

        stage('Canary deployment') {
            when {
                expression { params.DEPLOY_TARGET == 'canary' }
            }
            steps {
                sh '''#!/bin/bash
                    set -euo pipefail
                    set +x
                    bash deploy/cloudops-ssm-deploy.sh deploy i-02777a62f2a65bc1e
                    mkdir -p reports
                    printf '{"target":"canary","instance":"i-02777a62f2a65bc1e","commit":"%s","image":"%s","result":"verified"}\\n' \\
                        "$GIT_COMMIT_FULL" "$ECR_DEPLOY_IMAGE" > reports/deployment-evidence.json
                '''
            }
        }

        stage('ASG rolling deployment') {
            when {
                expression { params.DEPLOY_TARGET == 'asg' }
            }
            steps {
                withEnv(["ASG_AMI_REVIEWED=${params.ASG_AMI_REVIEWED}"]) {
                    sh '''#!/bin/bash
                        set -euo pipefail
                        set +x
                        bash deploy/cloudops-asg-rollout.sh
                    '''
                }
            }
        }

        stage('Post-deployment extension point - no-op') {
            steps {
                echo 'No additional production action is configured after the selected deployment target.'
            }
        }
    }

    post {
        always {
            script {
                if (fileExists('reports/pytest.xml')) {
                    junit allowEmptyResults: true, testResults: 'reports/pytest.xml'
                }
            }
            archiveArtifacts artifacts: 'reports/**', allowEmptyArchive: true, fingerprint: true
        }
        success {
            echo 'CI and configured security gates completed successfully. No production deployment was performed.'
        }
    }
}
