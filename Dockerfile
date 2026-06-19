# Minimal image to run the Claude usage widget headless (e.g. on a homelab box).
# Provide the token via CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) so no
# Claude Code install / credentials file is needed inside the container.
FROM python:3.12-slim

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md ./
COPY usage_widget ./usage_widget
RUN pip install --no-cache-dir --no-deps .

# Loop forever; interval/panel come from env (see .env.example) or compose.
ENTRYPOINT ["python", "-m", "usage_widget"]
