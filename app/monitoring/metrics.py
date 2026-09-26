from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST, REGISTRY

http_requests = Counter("cloudops_http_requests_total", "HTTP responses", ["method", "endpoint", "status"])
http_errors = Counter("cloudops_http_errors_total", "HTTP error responses", ["status"])
request_duration = Histogram("cloudops_http_request_duration_seconds", "HTTP request duration", ["method", "endpoint"])
login_attempts = Counter("cloudops_login_attempts_total", "Login attempts")
login_success = Counter("cloudops_login_success_total", "Successful logins")
login_failure = Counter("cloudops_login_failure_total", "Failed logins")
db_errors = Counter("cloudops_db_errors_total", "Database failures")
s3_upload = Counter("cloudops_s3_upload_total", "Successful uploads")
s3_upload_failures = Counter("cloudops_s3_upload_failures_total", "Failed uploads")

def response(): return generate_latest(REGISTRY), 200, {"Content-Type": CONTENT_TYPE_LATEST}
