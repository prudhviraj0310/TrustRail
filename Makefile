.PHONY: install run test demo lint fmt migrate compose-up compose-down clean

install:       ## Create a venv and install dependencies
	python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -r requirements.txt

run:           ## Run the API locally (SQLite, auto-reload)
	.venv/bin/uvicorn app.main:app --reload

test:          ## Run the full test suite
	.venv/bin/python -m pytest

demo:          ## Run the end-to-end demo against a running server
	.venv/bin/python scripts/cli_demo.py

lint:          ## Ruff lint
	.venv/bin/ruff check app tests

fmt:           ## Ruff format
	.venv/bin/ruff format app tests

migrate:       ## Autogenerate + apply an Alembic migration (Postgres prod)
	.venv/bin/alembic revision --autogenerate -m "schema" && .venv/bin/alembic upgrade head

compose-up:    ## Start the Postgres-backed stack
	docker compose up --build

compose-down:  ## Stop the stack
	docker compose down

clean:         ## Remove local SQLite DB and caches
	rm -f trustrail.db && find . -type d -name __pycache__ -prune -exec rm -rf {} +
