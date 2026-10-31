# Targets for later phases are wired now and start working once their phase builds the script.
VENV   := .venv
PY     := $(VENV)/bin/python
PORT   := 7860
CONFIG ?= D

.PHONY: setup check-env data ingest graph run test lint eval ragas safety load docker deploy

setup:            ## venv + pinned deps + spaCy model
	uv venv --python 3.11 $(VENV)
	VIRTUAL_ENV=$(VENV) uv pip sync requirements.lock
	VIRTUAL_ENV=$(VENV) uv pip install pip
	$(PY) -m spacy download en_core_web_lg

check-env:        ## verify tooling + every external credential
	$(PY) scripts/check_env.py

data:             ## Phase 2: synthetic Acme corpus
	$(PY) data/generate_synthetic.py

ingest:           ## Phase 2/3: clean, metadata, SQLite, Qdrant + BM25 indexes
	$(PY) -m ingestion.run --build-indexes

graph:            ## Phase 4: LLM extraction (cached) -> CSVs, then constraints, backbone, extracted, MENTIONS
	$(PY) -m ingestion.extract_graph
	$(PY) -m ingestion.load_graph

run:
	$(VENV)/bin/uvicorn app.main:app --reload --port $(PORT)

test:
	$(PY) -m pytest -q

lint:
	$(VENV)/bin/ruff check .

eval:             ## Phase 10: Promptfoo regression suite (starts its own API on :7860; stop `make run` first)
	$(PY) eval/promptfoo/run_promptfoo.py

ragas:            ## Phase 6/10: make ragas CONFIG=A|B|C|D
	$(PY) eval/ragas/run_ragas.py --config $(CONFIG)

safety:           ## Phase 10: adversarial + benign safety run (starts its own API; stop `make run` first)
	$(PY) eval/safety/run_safety.py

load:             ## Phase 11: Locust ramp test (API must be running)
	$(VENV)/bin/locust -f loadtest/locustfile.py --headless -u 10 -r 0.05 -t 3m --host http://localhost:$(PORT)

docker:
	docker build -t eka .

deploy:           ## Phase 12: upload git HEAD to the Hugging Face Space (D-029; see scripts/deploy_space.py)
	$(PY) scripts/deploy_space.py push
