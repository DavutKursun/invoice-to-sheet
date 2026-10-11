.PHONY: setup setup-dev test data run eval-dry eval app

PY ?= python

setup:
	$(PY) -m pip install -r requirements.txt
setup-dev:
	$(PY) -m pip install -r requirements-dev.txt
test:
	$(PY) -m pytest -q
data:             ## regenerate the synthetic labelled dataset
	$(PY) scripts/make_synthetic.py --n 80 --out data/synthetic --seed 7
run:              ## make run DIR=path/to/invoices
	$(PY) scripts/run_folder.py $(DIR)
eval-dry:
	$(PY) scripts/eval.py --dry-run --split dev
eval:
	$(PY) scripts/eval.py --split dev --delay 2
app:
	$(PY) app.py
