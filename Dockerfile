# CPU image (audit #102). Build:  docker build -t sovereign-optimizer:0.2.0 .
# Run:   docker run -p 8000:8000 -v sovdata:/data sovereign-optimizer:0.2.0
# Air-gapped site: docker save / docker load the image; no registry or internet needed at runtime.
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SOVEREIGN_DATA=/data SOVEREIGN_SKU=SKU-2
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app /data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
USER app
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).status == 200 else 1)"
CMD ["python", "-m", "uvicorn", "app:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
