FROM registry.access.redhat.com/ubi9/python-312:latest

# Run as root *inside* the container: with rootless podman that is the host user,
# so files in the mounted data folders belong to the service user.
USER 0
WORKDIR /opt/zammad-archive
COPY pyproject.toml README.md LICENSE ./
COPY zammad_archive ./zammad_archive
RUN pip install --no-cache-dir . && rm -rf /root/.cache

WORKDIR /work
ENTRYPOINT ["zammad-archive", "-c", "/work/config.yaml"]
CMD ["check"]
