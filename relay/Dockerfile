FROM python:3.12-slim
WORKDIR /app
COPY relay.py .
ENV PORT=8080 DB_PATH=/data/queue.db
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5)"
CMD ["python", "relay.py"]
