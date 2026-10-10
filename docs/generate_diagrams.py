from pathlib import Path
import html
out=Path('docs/diagrams');out.mkdir(parents=True,exist_ok=True)
class Diagram:
 def __init__(self,title,subtitle):
  self.parts=['<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="900" viewBox="0 0 1280 900" role="img">',f'<title>{html.escape(title)}</title>',f'<desc>{html.escape(subtitle)}</desc>','<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#475569"/></marker></defs>','<rect width="1280" height="900" fill="#f8fafc"/>']
  self.text(40,48,title,30,'#0f172a');self.text(40,80,subtitle,17,'#475569')
 def text(self,x,y,t,size=18,color='#0f172a'):
  self.parts.append(f'<text x="{x}" y="{y}" font-family="DejaVu Sans, Arial, sans-serif" font-size="{size}" fill="{color}">{html.escape(t)}</text>')
 def box(self,x,y,w,h,title,lines=(),color='#dbeafe'):
  self.parts.append(f'<g><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{color}" stroke="#94a3b8"/>');self.text(x+16,y+30,title,20)
  for i,line in enumerate(lines):self.text(x+16,y+58+i*24,line,16)
  self.parts.append('</g>')
 def arrow(self,x,y,xx,yy,label=''):
  self.parts.append(f'<path d="M{x},{y} L{xx},{yy}" stroke="#475569" stroke-width="2" fill="none" marker-end="url(#arrow)"/>')
  if label:self.text(min(x,xx)+10,min(y,yy)-9,label,15)
 def save(self,name):
  self.text(40,862,'Original ApexForge360 diagram • Reference examples only • No live AWS inventory verified',16,'#64748b');self.parts.append('</svg>');(out/f'{name}.svg').write_text('\n'.join(self.parts))
  import cairosvg
  cairosvg.svg2png(bytestring='\n'.join(self.parts).encode(),write_to=str(out/f'{name}.png'))
D=Diagram('AWS architecture — one private application EC2','Reference VPC 10.0.0.0/16 • Two Availability Zones • One Single-AZ MariaDB RDS')
D.box(40,110,1200,80,'Public services',['Internet → HTTPS ALB | IAM identity / roles | ECR immutable images | Secrets Manager | Systems Manager | CloudWatch'],'#e2e8f0')
D.box(40,215,580,90,'AZ A — public 10.0.1.0/24',['ALB node • Optional NAT A • Internet Gateway route'],'#dcfce7');D.box(660,215,580,90,'AZ B — public 10.0.2.0/24',['ALB node • Optional NAT B • Internet Gateway route'],'#dcfce7')
D.box(40,360,580,140,'AZ A — private application 10.0.11.0/24',['ONE application EC2 • Gunicorn :5000','Candidate :5001 is local-only • IAM application role','SSM agent • CloudWatch / Alloy logs'],'#dbeafe');D.box(660,360,580,140,'AZ B — private application 10.0.12.0/24',['Jenkins + SonarQube EC2 • IAM tools role','Observability EC2 • Prometheus / Grafana / Loki','Private SSM / AWS API endpoints or NAT egress'],'#dbeafe');D.arrow(300,305,300,360,'ALB → app :5000')
D.box(40,565,580,110,'AZ A — private database 10.0.21.0/24',['ONE Single-AZ MariaDB RDS instance','No public address • DB ingress :3306 from app SG'],'#fef3c7');D.box(660,565,580,110,'AZ B — private database 10.0.22.0/24',['Second member of the same DB subnet group','No second database instance or standby'],'#fef3c7');D.arrow(300,500,300,565,'app → MariaDB :3306')
D.box(40,720,1200,90,'Administration and delivery',['Administrator → SSM Session Manager • Jenkins → SSM Run Command • No inbound SSH or bastion','Private app pulls approved ECR digest; retrieves secrets using its EC2 role; logs to CloudWatch.'],'#ede9fe');D.save('01-architecture')
D=Diagram('Six-subnet VPC and routing','Example CIDRs / AZ labels • Choose two available AZs in your own region • No live IDs')
for x,az,public,app,db in [(40,'AZ A','10.0.1.0/24','10.0.11.0/24','10.0.21.0/24'),(660,'AZ B','10.0.2.0/24','10.0.12.0/24','10.0.22.0/24')]:
 D.box(x,120,580,110,az+' — public '+public,['Public route table: 10.0.0.0/16 → local','0.0.0.0/0 → IGW • ALB + optional same-AZ NAT'],'#dcfce7')
 D.box(x,300,580,130,az+' — private app '+app,['Application route table: VPC → local','Optional 0.0.0.0/0 → same-AZ NAT','Interface endpoints + S3 gateway endpoint alternative'],'#dbeafe');D.arrow(x+280,300,x+280,230,'NAT egress when configured')
 D.box(x,510,580,110,az+' — private DB '+db,['Database route table: 10.0.0.0/16 → local only','Single DB subnet group covers both DB subnets'],'#fef3c7')
D.box(40,680,1200,140,'Reachability matters',['IGW attached to VPC; a private EC2 has no public IP and cannot use IGW directly.','Endpoints: SSM / ssmmessages / ECR API + DKR / Secrets Manager / CloudWatch Logs; S3 for ECR layers.','Endpoint-only hosts still need approved mirrors or egress for Ubuntu packages, Docker registry and GitHub.','DNS support / hostnames and endpoint private DNS must be enabled.'],'#ede9fe');D.save('02-vpc-routing')
D=Diagram('One RDS instance, two DB subnet-group members','Reference VPC 10.0.0.0/16 • Single-AZ explicitly selected • No replica implied')
D.box(400,120,480,100,'Private application EC2',['Only app SG may connect to MariaDB TCP 3306']);D.arrow(640,220,640,285,'Private TCP 3306; review TLS')
D.box(40,285,1200,410,'ONE RDS DB subnet group — two Availability Zones',[], '#fef3c7')
D.box(80,365,520,230,'AZ A • DB subnet 10.0.21.0/24',['ONE MariaDB DB instance','MultiAZ=false • PubliclyAccessible=false','Encrypted storage • Backups / retention','Endpoint remains a DNS name'],'#fff7ed')
D.box(680,365,520,230,'AZ B • DB subnet 10.0.22.0/24',['Subnet-group member only','No running RDS instance in this subnet','No standby in this Single-AZ design','Keep a subnet in a second AZ for RDS'],'#fff7ed')
D.box(40,735,1200,85,'Availability trade-off',['A database or application instance outage interrupts service; this is a learning design with bounded rollback.'],'#e2e8f0');D.save('03-rds')
D=Diagram('Jenkins DevSecOps pipeline','Default: AWS_OPERATIONS=false / DEPLOY_TARGET=none • Trusted Linux Docker agent only')
rows=[('Checkout / environment','Canonical commit and branch • main-only AWS operations'),('Gitleaks → dependencies → pytest','Leaked secrets and failing tests stop the pipeline'),('Sonar Scanner → Jenkins Quality Gate','Persistent workspace metadata • webhook • abortPipeline=true'),('Trivy filesystem → Docker build → Trivy image','Complete reports; fixable HIGH/CRITICAL block; misconfiguration gate retained'),('Security artifacts → restricted AWS approval → immutable ECR','IAM role / account checks • matching existing SHA tag reused; conflicts fail'),('SSM preflight → restricted deployment approval → cutover','Exact digest • approved single instance • readiness / Docker / ALB • rollback')]
for i,(a,b) in enumerate(rows):
 y=110+i*112;D.box(100,y,1080,84,a,[b], '#dbeafe' if i<4 else '#dcfce7')
 if i<5:D.arrow(640,y+84,640,y+112)
D.save('04-pipeline')
D=Diagram('SSM administration and deployment paths','No SSH port 22 • No bastion • Private EC2 nodes initiate encrypted outbound connections')
D.box(40,130,520,130,'Administrator / IAM Identity Center',['MFA + short-lived session','Session Manager session / localhost port forwarding'],'#ede9fe');D.box(720,130,520,130,'Jenkins EC2 / tools instance role',['Restricted approval before AWS operations','SSM Run Command to ONE inventoried instance'],'#dbeafe')
D.arrow(300,260,300,345,'HTTPS 443');D.arrow(980,260,980,345,'AWS API HTTPS 443')
D.box(40,345,1200,130,'Systems Manager control and message services',['ssm + ssmmessages • IAM permissions • Session logging / auditing','Private interface endpoints or approved NAT egress • Endpoint SG allows TCP 443 from node SGs'],'#dcfce7')
D.arrow(300,545,300,475,'Agent outbound 443');D.arrow(980,545,980,475,'Agent outbound 443')
D.box(40,545,520,140,'Private Jenkins / monitoring EC2',['Browser UI via Session Manager forwarding','Bind management UIs to loopback / private network'],'#dbeafe');D.box(720,545,520,140,'Private application EC2',['SSM Agent Online • IMDSv2 instance role','Run Command checks scripts by commit + SHA256'],'#dbeafe')
D.box(40,735,1200,85,'Inbound administration rule',['NONE. Do not add public 22, 8080, 9000, 3000, 9090 or 3100 rules.'],'#fef3c7');D.save('05-ssm')
D=Diagram('Security group communication contract','Source security groups for internal traffic • Stateful response traffic • No inbound SSH')
rows=[('Internet → ALB SG','TCP 443 from clients; optional TCP 80 redirects to HTTPS'),('ALB SG → application SG','TCP 5000 only; candidate port 5001 binds loopback and is not admitted'),('Observability SG → application SG','TCP 5000 for metrics / ready probes; optional 9100 only if exporter enabled'),('Application SG → database SG','TCP 3306 only; one private MariaDB RDS; no CIDR-wide DB ingress'),('Application SG → observability SG','Private Loki TCP 3100 only if Alloy enabled; protect ingestion / TLS as appropriate'),('Node SGs → endpoint SG / approved egress','TCP 443 for AWS APIs; DNS through VPC resolver; package egress explicitly reviewed')]
for i,(a,b) in enumerate(rows):D.box(40,120+i*112,1200,88,a,[b], '#dcfce7' if i<1 else '#dbeafe')
D.save('06-security-groups')
D=Diagram('Immutable ECR → private EC2 cutover and rollback','Candidate-first • Same EC2 instance • Exact canonical container identity and configuration retained')
rows=[('1 • Publish / verify ECR','Immutable SHA tag → digest • Built/scanned image ID matches published image'),('2 • Preapproval SSM checks','Runtime file root:root 0600 • Stable signing secret • ECR / ALB permissions • SSM Online'),('3 • Candidate on localhost :5001','Host network / IAM role • Health + readiness + Docker + route • Previous :5000 stays serving'),('4 • Cutover only after candidate succeeds','Recheck prior full ID / name / image / network / ports • Stop and retain prior container'),('5 • Production on :5000','Same approved digest • Docker health / /health / /ready / application route / ALB healthy'),('6 • Failure recovery','Candidate failure leaves prior untouched; production failure restores exact original container')]
for i,(a,b) in enumerate(rows):
 y=110+i*112;D.box(80,y,1120,84,a,[b], '#fef3c7' if i==5 else '#dbeafe')
 if i<5:D.arrow(640,y+84,640,y+112)
D.save('07-deployment-rollback')
D=Diagram('Monitoring and logs','Reference private observability EC2 • Low-cardinality metrics • Secret-safe logs')
D.box(40,140,500,170,'ONE application EC2',['Flask /metrics • /health • /ready','Docker journald → optional Alloy collector','Deployment diagnostic log → CloudWatch Agent'],'#dbeafe');D.box(740,140,500,170,'Private observability EC2',['Prometheus scrapes :5000 • optional node exporter','Blackbox readiness probes • Grafana dashboards','Loki receives scoped Alloy log stream'],'#dcfce7');D.arrow(740,220,540,220,'private metrics');D.arrow(540,285,740,285,'private log ingestion')
D.box(40,430,500,150,'CloudWatch Logs',['Pre-created app log group • retention policy','Controlled health/deployment diagnostics','IAM role can write only approved streams'],'#fef3c7');D.arrow(280,310,280,430,'HTTPS 443')
D.box(740,430,500,150,'Administrator',['SSM localhost port forward → Grafana :3000','Dashboards show measured service signals','CloudWatch via authorized AWS console / CLI'],'#ede9fe');D.arrow(990,430,990,310,'SSM UI access')
D.box(40,680,1200,140,'Privacy and accuracy',['No request bodies, passwords, connection strings, uploaded names or secret environment values in logs.','Bounded endpoint labels; no arbitrary path / request ID metric labels.','A successful scrape is not proof of ALB readiness. Test alert delivery and failure recovery independently.'],'#e2e8f0');D.save('08-monitoring')