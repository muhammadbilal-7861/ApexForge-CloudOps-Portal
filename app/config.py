import os
import secrets


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280, "pool_timeout": 5, "connect_args": {"connect_timeout": 5}}
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    PREFERRED_URL_SCHEME = "https" if SESSION_COOKIE_SECURE else "http"
    APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
    FLASK_ENV = os.getenv("FLASK_ENV", "production")
