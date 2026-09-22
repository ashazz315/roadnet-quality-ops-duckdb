FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ROADINSIGHT_MODE=server ROADINSIGHT_DATA_DIR=/var/data/roadinsight PORT=8501
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 roadinsight
COPY . .
RUN mkdir -p /var/data/roadinsight && chown -R roadinsight:roadinsight /var/data
USER roadinsight
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8501')+'/_stcore/health',timeout=3)"
CMD ["python", "scripts/13_serve.py"]
