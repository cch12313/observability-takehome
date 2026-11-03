FROM python:3.11-slim
WORKDIR /app
COPY api-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common/ ./common/
COPY api-service/app.py .
ENV PYTHONPATH=/app/common
EXPOSE 8000
CMD ["opentelemetry-instrument", "uvicorn", "app:app", "--host", "0.0.0.0", "--log-config", "/app/common/log_config.json", "--port", "8000"]
