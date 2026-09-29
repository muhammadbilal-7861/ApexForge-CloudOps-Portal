pipeline {
    agent { label 'linux && docker' }

    options {
        skipDefaultCheckout(true)
        timestamps()
        disableConcurrentBuilds()
        timeout(time: 60, unit: 'MINUTES')
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
    }

    environment {
        PYTHON_IMAGE = 'python:3.12-slim'
        GITLEAKS_IMAGE = 'ghcr.io/gitleaks/gitleaks:v8.29.1'
        TRIVY_IMAGE = 'ghcr.io/aquasecurity/trivy:0.74.0'
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
                    env.APP_IMAGE_REF = "${env.APP_IMAGE_NAME}:${env.BUILD_NUMBER}-${env.GIT_COMMIT_SHORT}"
                }
                sh '''#!/bin/sh
                    set -eu
                    mkdir -p reports .pip-cache .trivy-cache
                    printf '%s\n' '--- ApexForge CloudOps CI build ---'
                    printf 'Job: %s\nBuild: %s\nBranch: %s\nCommit: %s\nAgent: %s\n' \
                        "$JOB_NAME" "$BUILD_NUMBER" "${BRANCH_NAME:-unknown}" "$GIT_COMMIT_SHORT" "$(uname -a)"
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
                        sh -ec 'python -m venv --clear "$WORKSPACE/.ci-venv" && "$WORKSPACE/.ci-venv/bin/pip" install --disable-pip-version-check -r requirements.txt && "$WORKSPACE/.ci-venv/bin/pip" check'
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
                    docker run --rm \
                        --volumes-from jenkins \
                        --workdir "$WORKSPACE" \
                        --env TRIVY_CACHE_DIR="$WORKSPACE/.trivy-cache" \
                        "$TRIVY_IMAGE" fs \
                        --scanners vuln,misconfig \
                        --skip-dirs .git,.ci-venv,.pip-cache,.trivy-cache,.sonar,.scannerwork,reports,.pytest_cache,__pycache__,.venv,venv \
                        --severity "$TRIVY_SEVERITY" \
                        --exit-code 0 \
                        --format table \
                        .
                    docker run --rm \
                        --volumes-from jenkins \
                        --workdir "$WORKSPACE" \
                        --env TRIVY_CACHE_DIR="$WORKSPACE/.trivy-cache" \
                        "$TRIVY_IMAGE" fs \
                        --scanners vuln,misconfig \
                        --skip-dirs .git,.ci-venv,.pip-cache,.trivy-cache,.sonar,.scannerwork,reports,.pytest_cache,__pycache__,.venv,venv \
                        --severity "$TRIVY_SEVERITY" \
                        --exit-code "$TRIVY_EXIT_CODE" \
                        --format json \
                        --output "$WORKSPACE/reports/trivy-filesystem.json" \
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
                                --workdir "$WORKSPACE" \
                                --env TRIVY_CACHE_DIR="$WORKSPACE/.trivy-cache" \
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
                    docker build --pull --tag "$APP_IMAGE_REF" .
                '''
            }
        }

        stage('Trivy Docker image vulnerability scan') {
            steps {
                sh '''#!/bin/sh
                    set -eu
                    docker run --rm \
                        --volumes-from jenkins \
                        --workdir "$WORKSPACE" \
                        --env TRIVY_CACHE_DIR="$WORKSPACE/.trivy-cache" \
                        "$TRIVY_IMAGE" image \
                        --image-src docker \
                        --scanners vuln \
                        --severity "$TRIVY_SEVERITY" \
                        --exit-code 0 \
                        --format table \
                        "$APP_IMAGE_REF"
                    docker run --rm \
                        --volumes-from jenkins \
                        --workdir "$WORKSPACE" \
                        --env TRIVY_CACHE_DIR="$WORKSPACE/.trivy-cache" \
                        "$TRIVY_IMAGE" image \
                        --image-src docker \
                        --scanners vuln \
                        --severity "$TRIVY_SEVERITY" \
                        --exit-code "$TRIVY_EXIT_CODE" \
                        --format json \
                        --output "$WORKSPACE/reports/trivy-image.json" \
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
                branch 'main'
            }
            steps {
                sh '''#!/bin/bash
                    set -euo pipefail
                    set +x

                    aws_cli() {
                        docker run --rm \
                            --network host \
                            --volumes-from jenkins \
                            --env AWS_REGION="$AWS_REGION" \
                            --env AWS_DEFAULT_REGION="$AWS_REGION" \
                            "$AWS_CLI_IMAGE" "$@"
                    }

                    caller_arn="$(aws_cli sts get-caller-identity --query Arn --output text)"
                    caller_account="$(aws_cli sts get-caller-identity --query Account --output text)"
                    case "$caller_arn" in
                        "arn:aws:sts::${AWS_ACCOUNT_ID}:assumed-role/${AWS_EXPECTED_ROLE}/"*) ;;
                        *) echo 'Refusing ECR push: EC2 instance role does not match the expected role.' >&2; exit 1 ;;
                    esac
                    if [ "$caller_account" != "$AWS_ACCOUNT_ID" ]; then
                        echo 'Refusing ECR push: AWS caller account does not match the configured account.' >&2
                        exit 1
                    fi

                    docker_config="$(mktemp -d /tmp/apexforge-docker-config.XXXXXX)"
                    trap 'rm -rf "$docker_config"' EXIT
                    export DOCKER_CONFIG="$docker_config"

                    aws_cli ecr get-login-password --region "$AWS_REGION" |
                        docker login --username AWS --password-stdin "$ECR_REGISTRY"
                    printf 'Publishing %s from build %s\n' "$ECR_REPOSITORY" "$BUILD_NUMBER"
                    docker tag "$APP_IMAGE_REF" "$ECR_URI:$GIT_COMMIT_SHORT"
                    docker push "$ECR_URI:$GIT_COMMIT_SHORT"
                '''
            }
        }

        stage('Production approval / deployment - placeholder') {
            steps {
                echo 'Placeholder only: no production approval or deployment is performed by this pipeline.'
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
