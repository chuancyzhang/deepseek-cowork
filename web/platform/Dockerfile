FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home cowork && mkdir /data && chown cowork:cowork /data
COPY server /app/server
COPY ui /app/ui
USER cowork
EXPOSE 8080
CMD ["python", "-m", "server", "serve", "--host", "0.0.0.0", "--port", "8080"]
