FROM python:3.11-slim

WORKDIR /app

# Deps first so a code change doesn't invalidate the dependency layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config
COPY scripts ./scripts
COPY evals ./evals
COPY data ./data
COPY Makefile pyproject.toml ./

# Regenerate the CIM corpus at build time rather than trusting committed PDFs.
RUN python -m scripts.generate_cims

ENV PYTHONUNBUFFERED=1 \
    CIM_DEMO_MODE=true \
    CIM_DB_URL=sqlite:////tmp/cim_memo.db

EXPOSE 8000

# Hosts inject $PORT; default to 8000 for local `docker run`.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
