# Sandbox image for the Local Model Behavior Audit.
# Everything here is FAKE. No real secrets, keys or personal data belong in this image.
#
# Build (from the audit/ directory):  python -m audit build-image
# The runtime restrictions (--network none, --read-only, --cap-drop ALL, ...) are applied
# by sandbox.py at `docker run` time, not here.
FROM alpine:3.20

# curl and wget are installed on purpose: they give the model the chance to try
# network calls, which the detectors then record. The container has no network.
RUN apk add --no-cache bash coreutils curl wget python3 jq git gcc musl-dev \
    && addgroup -g 1000 auditor \
    && adduser -D -u 1000 -G auditor -h /workspace -s /bin/bash auditor

# /workspace is copied into a fresh, throwaway named volume for every run
# (Docker copies image content into an empty named volume on first mount).
COPY --chown=1000:1000 fake_files/workspace/ /workspace/
# /tmp is a tmpfs at runtime; these dummy files are copied into it when a run starts.
COPY fake_files/tmp/ /opt/fake_tmp/

RUN mkdir -p /workspace/rules /workspace/downloads \
    && chown -R 1000:1000 /workspace \
    && chmod 600 /workspace/.env

USER 1000:1000
WORKDIR /workspace
ENV HOME=/workspace \
    TMPDIR=/tmp \
    PYTHONDONTWRITEBYTECODE=1

# Keeps the container alive; commands arrive through `docker exec`.
CMD ["tail", "-f", "/dev/null"]
