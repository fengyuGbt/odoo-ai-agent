# Image for the AI-agent sidecar of the docker-compose stack.
FROM python:3.12-slim

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# Project sources.
COPY . .

# Default: wait for Odoo, then run whatever command was passed.
ENTRYPOINT []
CMD ["python", "docker/wait_then_run.py", "python", "main.py"]
