# iRacingStats — stdlib-only Python, so there is nothing to install.
#
# The database is NOT baked in. It is mounted at /app/data, which is what makes
# the image shareable: the code is generic and every result in it belongs to
# whoever mounted the volume.
FROM python:3.12-slim

WORKDIR /app

COPY server.py /app/server.py
# Shipped so a recipient with no local Python can build their database through
# the image itself:
#   docker run --rm -v ./data:/app/data -v ~/Downloads:/in:ro IMAGE \
#     python3 load_iracing_data.py --exports /in --db /app/data/stats.db
COPY load_iracing_data.py /app/load_iracing_data.py
COPY web/ /app/web/

# Run unprivileged; the mounted data volume is read-only to the app anyway
# (server.py opens SQLite with mode=ro).
RUN useradd --system --uid 10001 --create-home appuser \
    && mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/api/bootstrap', timeout=4).status==200 else 1)"

CMD ["python3", "server.py", "--port", "8090", "--db", "/app/data/stats.db"]
