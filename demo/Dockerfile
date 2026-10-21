# Demo environment image: control plane, demo services, traffic generator and demo git repositories.
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app app
WORKDIR /opt/demo
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./demo
RUN python demo/repos/build_repos.py /repos && chown -R app:app /repos && mkdir -p /data && chown app:app /data
ENV PYTHONPATH=/opt/demo PYTHONUNBUFFERED=1
USER app
