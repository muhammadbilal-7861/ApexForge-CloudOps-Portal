# syntax=docker/dockerfile:1.19@sha256:b6afd42430b15f2d2a4c5a02b919e98a525b785b1aaff16747d2f623364e39b6
FROM python:3.12-slim@sha256:6b1f85a08c199d29d5b6d71ab9c27bd5b3b393492e01216a15758ff69c4be8b8
ARG SOURCE_DATE_EPOCH
# Reviewed Debian security package: exact bytes, no mutable apt resolution.
RUN python -B -c "import urllib.request; urllib.request.urlretrieve('https://security.debian.org/debian-security/pool/updates/main/p/pcre2/libpcre2-8-0_10.46-1~deb13u3_amd64.deb', '/tmp/libpcre2.deb')" \
    && echo 'e226f661d918f04daf38cdbc4806b7ed7d6ef95c7eb0ade692fc350e31970040  /tmp/libpcre2.deb' | sha256sum --check --status \
    && dpkg -i /tmp/libpcre2.deb \
    && rm -f /tmp/libpcre2.deb /var/log/dpkg.log /var/cache/ldconfig/aux-cache
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=5000
WORKDIR /srv/cloudops
RUN groupadd --system --gid 999 app && useradd --system --uid 999 --gid app --no-log-init --home-dir /srv/cloudops app \
    && sed -i 's/^app:[^:]*:[^:]*:/app:!:0:/' /etc/shadow
COPY requirements.txt .
RUN pip install --no-cache-dir --no-compile --only-binary=:all: --require-hashes -r requirements.txt
COPY app ./app
COPY wsgi.py ./wsgi.py
USER app
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health',timeout=2)" || exit 1
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:5000", "--access-logfile", "-", "--error-logfile", "-", "wsgi:app"]
