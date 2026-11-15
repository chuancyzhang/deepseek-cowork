FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home cowork && mkdir /data && chown cowork:cowork /data
ARG COWORK_BUILD_VERSION=source
ARG COWORK_BUILD_SOURCE
LABEL org.opencontainers.image.version=$COWORK_BUILD_VERSION io.cowork.source-sha256=$COWORK_BUILD_SOURCE
ENV COWORK_BUILD_VERSION=$COWORK_BUILD_VERSION COWORK_BUILD_SOURCE=$COWORK_BUILD_SOURCE
COPY server /app/server
COPY ui /app/ui
USER cowork
EXPOSE 8080
CMD ["python", "-m", "server", "serve", "--host", "0.0.0.0", "--port", "8080"]
