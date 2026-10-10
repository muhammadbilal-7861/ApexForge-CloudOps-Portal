"""Single-instance topology, initial-install and runtime preparation regressions."""
import json
from pathlib import Path
import pytest
from tests.test_deployment_foundation import ROOT, load_helper, fake_canary_host, run_fake_deploy


def snapshots(path):
    config=json.loads((ROOT/'tests/fixtures/deployment-config.json').read_text())
    alb='arn:example:alb'
    target=config['TARGET_GROUP_ARN']
    documents={
        'alb.json':{'LoadBalancers':[{'LoadBalancerName':config['ALB_NAME'],'VpcId':config['VPC_ID'],'LoadBalancerArn':alb,'SecurityGroups':['sg-alb']}]},
        'target-group.json':{'TargetGroups':[{'TargetGroupName':config['TARGET_GROUP_NAME'],'TargetGroupArn':target,'Port':5000,'HealthCheckPath':'/ready','VpcId':config['VPC_ID'],'LoadBalancerArns':[alb]}]},
        'listeners.json':{'Listeners':[{'DefaultActions':[{'TargetGroupArn':target}]}]},
        'observability-instance.json':{'Reservations':[{'Instances':[{'InstanceId':config['OBSERVABILITY_INSTANCE_ID'],'SecurityGroups':[{'GroupId':'sg-obs'}]}]}]},
        'canary-instance.json':{'Reservations':[{'Instances':[{'InstanceId':config['CANARY_INSTANCE_ID'],'State':{'Name':'running'},'VpcId':config['VPC_ID'],'SubnetId':config['PRIVATE_SUBNET_IDS'][0],'MetadataOptions':{'HttpTokens':'required'},'IamInstanceProfile':{'Arn':config['INSTANCE_PROFILE_ARN']},'SecurityGroups':[{'GroupId':config['APP_SECURITY_GROUP_ID']}]}]}]},
        'canary-ssm.json':{'InstanceInformationList':[{'PingStatus':'Online'}]},
        'rds.json':{'DBInstances':[{'DBInstanceIdentifier':config['RDS_INSTANCE_ID'],'DBInstanceStatus':'available','Engine':'mariadb','MultiAZ':False,'PubliclyAccessible':False,'DBSubnetGroup':{'VpcId':config['VPC_ID'],'Subnets':[{'SubnetIdentifier':f'subnet-0000000000000000{i}','SubnetAvailabilityZone':{'Name':f'us-east-1{az}'}} for i,az in [(3,'a'),(4,'b')]]}}]},
        'logs.json':{'logGroups':[{'logGroupName':config['LOG_GROUP_NAME']}]},
        'security-groups.json':{'SecurityGroups':[{'GroupId':config['APP_SECURITY_GROUP_ID'],'IpPermissions':[{'IpProtocol':'tcp','FromPort':5000,'ToPort':5000,'UserIdGroupPairs':[{'GroupId':'sg-alb'},{'GroupId':'sg-obs'}]}]}]}}
    for name,doc in documents.items():(path/name).write_text(json.dumps(doc))
    return documents,config


def test_single_instance_preflight_accepts_two_db_subnets_and_one_database(tmp_path):
    _,config=snapshots(tmp_path)
    helper=load_helper('cloudops-aws-preflight')
    target,groups=helper.validate_common(tmp_path,'canary')
    assert target == config['TARGET_GROUP_ARN']
    assert groups == [config['APP_SECURITY_GROUP_ID']]


@pytest.mark.parametrize('field,value',[('MultiAZ',True),('PubliclyAccessible',True),('Engine','mysql'),('DBInstanceStatus','stopped')])
def test_single_instance_preflight_rejects_unapproved_database(tmp_path,field,value):
    documents,_=snapshots(tmp_path)
    documents['rds.json']['DBInstances'][0][field]=value
    (tmp_path/'rds.json').write_text(json.dumps(documents['rds.json']))
    helper=load_helper('cloudops-aws-preflight')
    with pytest.raises(helper.PreflightError,match='Single-AZ'):
        helper.validate_common(tmp_path,'canary')


@pytest.mark.parametrize('change',['public_ip','ssh','profile','subnet'])
def test_private_ssm_only_instance_is_required(tmp_path,change):
    documents,_=snapshots(tmp_path)
    instance=documents['canary-instance.json']['Reservations'][0]['Instances'][0]
    if change=='public_ip':instance['PublicIpAddress']='192.0.2.1'
    if change=='profile':instance['IamInstanceProfile']['Arn']='arn:aws:iam::999999999999:instance-profile/ExampleAppRole'
    if change=='subnet':instance['SubnetId']='subnet-00000000000000009'
    if change=='ssh':documents['security-groups.json']['SecurityGroups'][0]['IpPermissions'].append({'IpProtocol':'tcp','FromPort':22,'ToPort':22,'IpRanges':[{'CidrIp':'0.0.0.0/0'}]})
    for name,doc in documents.items():(tmp_path/name).write_text(json.dumps(doc))
    helper=load_helper('cloudops-aws-preflight')
    with pytest.raises(helper.PreflightError):
        helper.validate_common(tmp_path,'canary')


@pytest.mark.parametrize('failure',[None,'candidate','production','alb'])
def test_explicit_initial_install_on_empty_host(fake_canary_host,failure):
    host=fake_canary_host
    state=json.loads(Path(host['state']).read_text());state['containers']={};Path(host['state']).write_text(json.dumps(state))
    host['env']['ALLOW_INITIAL_INSTALL']='true'
    if failure=='candidate':host['env']['TEST_CANDIDATE_READY']='false'
    if failure=='production':host['env']['TEST_PRODUCTION_READY']='false'
    if failure=='alb':host['env']['TEST_ALB_HEALTHY']='false'
    result=run_fake_deploy(host)
    containers=json.loads(Path(host['state']).read_text())['containers'].values()
    if failure is None:
        assert result.returncode==0, result.stdout+result.stderr
        production=next(c for c in containers if c['name']=='cloudops-app')
        assert production['running'] is True
        assert production['image']==host['image']
    else:
        assert result.returncode!=0
        assert not any(c['running'] for c in containers)


def test_empty_host_install_requires_approved_opt_in(fake_canary_host):
    host=fake_canary_host
    state=json.loads(Path(host['state']).read_text());state['containers']={};Path(host['state']).write_text(json.dumps(state))
    result=run_fake_deploy(host)
    assert result.returncode!=0
    assert 'explicit ALLOW_INITIAL_INSTALL=true' in result.stdout
    assert json.loads(Path(host['state']).read_text())['containers']=={}


def test_initial_install_refuses_unknown_containers(fake_canary_host):
    host=fake_canary_host
    state=json.loads(Path(host['state']).read_text())
    original=state['containers'].pop('legacy-1')
    original.update(name='unknown-workload', image='unrelated:1')
    state['containers']['unknown']=original
    Path(host['state']).write_text(json.dumps(state))
    host['env']['ALLOW_INITIAL_INSTALL']='true'
    result=run_fake_deploy(host)
    assert result.returncode!=0
    assert 'empty Docker container inventory' in result.stdout
    assert json.loads(Path(host['state']).read_text())==state


def test_tools_policy_cannot_provision_compute_or_execute_on_arbitrary_instances():
    policy=json.loads((ROOT/'deploy/iam/jenkins-cloudops-deploy-policy.json').read_text())
    actions=set()
    for statement in policy['Statement']:
        value=statement['Action']
        actions.update([value] if isinstance(value,str) else value)
    compute={action for action in actions if action.startswith('ec2:')}
    assert compute=={'ec2:DescribeInstances','ec2:DescribeSecurityGroups','ec2:DescribeSubnets','ec2:DescribeVpcs'}
    commands=[s for s in policy['Statement'] if s['Action']=='ssm:SendCommand']
    assert len(commands)==1
    assert commands[0]['Resource'][1].endswith('/${CANARY_INSTANCE_ID}')


def test_visual_guide_has_complete_assets_links_and_lab_sections():
    from docs.validate_docs import validate
    links,diagrams,labs=validate()
    assert links>50
    assert diagrams==8
    assert labs==14


def test_runtime_preparation_uses_only_approved_instance_role_and_stable_secret():
    helper=load_helper('prepare-runtime')
    config=json.loads((ROOT/'tests/fixtures/deployment-config.json').read_text())
    secret='synthetic-test-signing-value-'+'x'*40
    def fetch(service,operation,*args):
        if service=='sts':return {'Account':config['AWS_ACCOUNT_ID'],'Arn':f"arn:aws:sts::{config['AWS_ACCOUNT_ID']}:assumed-role/{config['APP_ROLE_NAME']}/instance"}
        return {'SecretString':secret if args[-1]==config['SESSION_SECRET_NAME'] else json.dumps({'host':'db.example.invalid','username':'app','password':'synthetic','dbname':'cloudops'})}
    output=helper.runtime(config,fetch)
    assert 'USE_AWS_SECRETS=true\n' in output
    assert 'SECRET_KEY='+secret+'\n' in output
    assert 'DATABASE_URL' not in output
    config['AWS_ACCOUNT_ID']='999999999999'
    # Freeze the observed identity to the original account, rather than change it with config.
    wrong=lambda *args: {'Account':'123456789012','Arn':'arn:aws:sts::123456789012:assumed-role/ExampleAppRole/instance'}
    with pytest.raises(ValueError,match='role/account mismatch'):
        helper.runtime(config,wrong)
