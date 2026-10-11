FROM python:3.12-slim
RUN pip install --no-cache-dir fastapi==0.143.0 uvicorn==0.54.0 cryptography==50.0.2
WORKDIR /app
COPY server.py .
ENV XOD_RELAY_DB=/data/relay.db
VOLUME /data
EXPOSE 8790
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8790", "--proxy-headers"]
