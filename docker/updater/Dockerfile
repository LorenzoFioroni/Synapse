# The updater (docker/updater/updater.py, docs/updates.md): the only container with access to Docker.
# Built with the repository as its context, like the Synapse image.
FROM docker:29.9-cli
RUN apk add --no-cache python3 git
COPY docker/updater/updater.py /updater.py
# Docker's command-line tools keep their state in HOME; compose.yaml makes it a /tmp in memory.
ENV HOME=/tmp
EXPOSE 9000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --start-interval=2s --retries=3 \
    CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9000/health', timeout=4)"]
ENTRYPOINT ["python3", "-u", "/updater.py"]
