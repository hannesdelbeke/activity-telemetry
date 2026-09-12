FROM python:3.12-slim
WORKDIR /app
COPY src/activity_sink.py /app/activity_sink.py
EXPOSE 8788 8099
CMD ["python", "/app/activity_sink.py"]
