FROM python:3.11-slim

WORKDIR /app

COPY deploy/requirements.txt /tmp/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY app /app

ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "main.py"]
