.PHONY: install run ui test compile
install:
	python -m pip install -r requirements.txt
run:
	uvicorn app.main:app --reload
ui:
	streamlit run streamlit_app.py
test:
	pytest -q
compile:
	python -m compileall -q app streamlit_app.py
