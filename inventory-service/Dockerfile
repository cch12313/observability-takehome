FROM python:3.11-slim
WORKDIR /app
COPY inventory-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common/ ./common/
COPY inventory-service/app.py .
ENV PYTHONPATH=/app/common
EXPOSE 8001
CMD ["opentelemetry-instrument", "uvicorn", "app:app", "--host", "0.0.0.0", "--log-config", "/app/common/log_config.json", "--port", "8001"]
