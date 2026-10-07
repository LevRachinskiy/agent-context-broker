FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/service/.venv/bin:$PATH"
WORKDIR /service
COPY pyproject.toml uv.lock ./
COPY app ./app
RUN pip install --no-cache-dir uv==0.12.19 && uv sync --frozen --no-dev --no-editable \
    && useradd --create-home broker && chown -R broker:broker /service
USER broker
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
