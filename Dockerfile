FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/data

CMD ["python", "main.py"]
