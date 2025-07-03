FROM python:3.12-slim

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

WORKDIR /app
COPY main.py .

ENV PORT=8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
