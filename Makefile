.PHONY: setup test run docker-build docker-up docker-down lint
setup:
	python -m venv .venv
	.venv\\Scripts\\python -m pip install -r requirements.txt
test:
	python -m pytest -q
run:
	python -m flask --app wsgi run --host 0.0.0.0 --port 5000
docker-build:
	docker build -t apexforge-cloudops:local .
docker-up:
	docker compose up --build
docker-down:
	docker compose down
lint:
	python -m compileall -q app wsgi.py
