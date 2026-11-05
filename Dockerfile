FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY etl ./etl
COPY sql ./sql
COPY config ./config
COPY data/registry ./data/registry
ENTRYPOINT ["python", "-m", "etl"]
CMD ["--help"]
