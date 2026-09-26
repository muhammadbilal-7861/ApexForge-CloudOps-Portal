from tests.conftest import register, login

def test_health(client):
    response=client.get('/health'); assert response.status_code==200; assert response.json=={"status":"healthy","service":"cloudops-portal"}

def test_ready_sqlite(app, client):
    with app.app_context(): from app.extensions import db; db.create_all()
    response=client.get('/ready'); assert response.status_code==200; assert response.json["database"]=="connected"

def test_db_failure_keeps_liveness_and_fails_readiness(app, client):
    app.extensions["lab_db_failed"]=True
    assert client.get('/health').status_code==200
    response=client.get('/ready'); assert response.status_code==503
    assert response.json=={"status":"not_ready","database":"unavailable"}

def test_registration_login_protected_records(client):
    response=register(client); assert response.status_code==200
    response=client.get('/dashboard'); assert response.status_code==302
    response=login(client); assert response.status_code==200; assert b"Operations dashboard" in response.data
    response=client.post('/records/add', data={"title":"Failover notes","description":"RDS activity"}, follow_redirects=True)
    assert response.status_code==200 and b"Failover notes" in response.data

def test_metrics_and_404(client):
    assert client.get('/metrics').status_code==200
    assert b'cloudops_http_requests_total' in client.get('/metrics').data
    assert client.get('/definitely-missing').status_code==404

def test_record_owner_cannot_delete_another_users_record(client, app):
    register(client); login(client)
    client.post('/records/add', data={"title":"private","description":""})
    client.get('/logout'); register(client,"another","another@example.com"); login(client,"another")
    response=client.post('/records/1/delete'); assert response.status_code==403

def test_login_metrics(client):
    client.post('/login', data={"username":"missing","password":"no"})
    assert client.get('/metrics').status_code==200
