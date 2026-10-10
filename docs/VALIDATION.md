# Single-EC2 validation record

Validation in progress on feat/single-ec2-illustrated-guide. Results below are local evidence, not AWS or Jenkins acceptance.

- Initial retained suite:107 passed; expanded single-instance suite:122 passed, no skips, isolated root Linux Docker container without network/credentials/socket.
- Jenkinsfile compiled as full Groovy4 on JDK17;18 embedded shell scripts passed syntax parsing.
- Eight original editable SVG/PNG pairs generated; architecture and pipeline PNGs visually inspected.
- Final full suite, policy/schema/lint/link checks, fresh clone and repeated secret audit are recorded after completion.

No live AWS/Jenkins resources were read or changed; no instance launch, role update, RDS operation, secret rotation or deployment occurred. All network/AMI/account/host values in the guide are reference examples or operator-resolved values, not claimed observed live metadata. Current guide commands need independent lab acceptance for EC2/IAM/SSM/ALB/RDS/network/packages/agents, Jenkins plugins/webhook and monitoring alerts. Local Groovy parsing does not substitute for a configured Jenkins Declarative/plugin linter.
