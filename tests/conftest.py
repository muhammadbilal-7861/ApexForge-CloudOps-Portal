import pytest
from app import create_app
from app.extensions import db

@pytest.fixture
def app():
    app=create_app({"TESTING":True,"WTF_CSRF_ENABLED":False,"SECRET_KEY":"test-secret","SQLALCHEMY_DATABASE_URI":"sqlite://","SQLALCHEMY_ENGINE_OPTIONS":{},"APP_VERSION":"test"})
    with app.app_context(): db.create_all()
    yield app
    with app.app_context(): db.session.remove(); db.drop_all()

@pytest.fixture
def client(app): return app.test_client()

def register(client, username="dev", email="dev@example.com", password="long-password-123"):
    return client.post("/register", data={"username":username,"email":email,"password":password}, follow_redirects=True)

def login(client, username="dev", password="long-password-123"):
    return client.post("/login", data={"username":username,"password":password}, follow_redirects=True)
