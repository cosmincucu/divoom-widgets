# Minimal image to run the widget framework headless (e.g. on a homelab box).
# Claude credentials via a mounted directory holding the container's own Claude
# Code login (see README "Deployment"; `claude setup-token` tokens cannot read
# usage); HA token via HA_TOKEN or a mounted file pointed at by HA_TOKEN_FILE.
FROM python:3.12-slim

# DejaVu fonts: the render falls back to DejaVuSans[-Bold].ttf on non-Windows.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Unbuffered stdout so `docker logs` shows cycles as they happen.
ENV PYTHONUNBUFFERED=1

# Install deps first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Run the package straight from the workdir — no install step needed, and the
# desktop-only divoom_keeper tray app stays out of the image (.dockerignore).
COPY divoom_widgets ./divoom_widgets

# Loop forever; widgets/panels/intervals come from env (see .env.example).
ENTRYPOINT ["python", "-m", "divoom_widgets"]
