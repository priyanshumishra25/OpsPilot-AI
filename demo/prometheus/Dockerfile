FROM prom/prometheus:v3.15.0
COPY prometheus.yml /etc/prometheus/prometheus.yml
CMD ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.retention.time=2d"]
