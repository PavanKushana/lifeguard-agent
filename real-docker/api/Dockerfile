# The shop api for real Docker mode. Built twice by setup.sh: version 2.3.1 (good) and 2.3.2 (the bad deploy).
FROM python:3.13-slim
ARG APP_VERSION=2.3.1
ENV APP_VERSION=${APP_VERSION} PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir "psycopg[binary]==3.2.3" "redis==5.2.1"
COPY app.py /app/app.py
CMD ["python", "/app/app.py"]
