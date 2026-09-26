import json
import logging
import os

log = logging.getLogger(__name__)


def database_config():
    if os.getenv("USE_AWS_SECRETS", "false").lower() == "true":
        try:
            import boto3
            name = os.environ["AWS_SECRET_NAME"]
            response = boto3.client("secretsmanager", region_name=os.getenv("AWS_REGION") or None).get_secret_value(SecretId=name)
            value = json.loads(response["SecretString"])
            return {"host": value["host"], "port": int(value.get("port", 3306)), "name": value.get("dbname", value.get("database")), "user": value["username"], "password": value["password"]}
        except Exception:
            log.exception("Secrets Manager database configuration unavailable; secret value omitted")
            raise RuntimeError("Unable to load database configuration from Secrets Manager") from None
    return {"host": os.getenv("DB_HOST", "127.0.0.1"), "port": int(os.getenv("DB_PORT", "3306")), "name": os.getenv("DB_NAME", "cloudops"), "user": os.getenv("DB_USER", "cloudops"), "password": os.getenv("DB_PASSWORD", "cloudops-local")}


def database_uri():
    from urllib.parse import quote_plus
    c = database_config()
    return f"mysql+pymysql://{quote_plus(c['user'])}:{quote_plus(c['password'])}@{c['host']}:{c['port']}/{quote_plus(c['name'])}?charset=utf8mb4"
