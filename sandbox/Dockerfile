# Sandbox image: Python + pytest only. Every test run starts a fresh container from it with
# --network none, --read-only, resource limits, --cap-drop ALL, and this non-root user.
# Build: docker build -t test-repair-sandbox:latest sandbox/
FROM python:3.12-slim

RUN pip install --no-cache-dir pytest==9.1.1 \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox

USER 10001:10001
WORKDIR /tmp
