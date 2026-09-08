FROM python:3.11-slim

# The app MUST bind 0.0.0.0 inside the container: Docker's port mapping
# connects to the container's eth0 IP, so a loopback-only (127.0.0.1) bind
# would silently break -p 5000:5000 (same "dark page" symptom as WSL).
ENV AICR_HOST=0.0.0.0 \
    AICR_PORT=5000 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Only Flask is needed; everything else is stdlib.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code. exports/ is deliberately NOT copied — it is your personal chat
# data and is mounted read-only at runtime (see docker-compose.yml).
COPY app.py .
COPY parsers/ parsers/
COPY static/ static/

# The exports dir must exist (mount target); make an empty placeholder too
# so the app boots even if not mounted.
RUN mkdir -p exports

EXPOSE 5000

CMD ["python3", "app.py"]