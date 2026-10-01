FROM python:3.12-slim
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=5000
WORKDIR /srv/cloudops
RUN groupadd --system app && useradd --system --gid app --home-dir /srv/cloudops app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY wsgi.py ./wsgi.py
USER app
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health',timeout=2)" || exit 1
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:5000", "--access-logfile", "-", "--error-logfile", "-", "wsgi:app"]
