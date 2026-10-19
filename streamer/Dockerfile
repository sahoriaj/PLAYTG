FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
ENV HOST=0.0.0.0 SESSION_DIR=/tmp/session
# Render/Koyeb set PORT automatically; server.py reads it
CMD ["python", "server.py"]
