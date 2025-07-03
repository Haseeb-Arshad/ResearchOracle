FROM python:3.12-slim
# 1) install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt
# 2) copy app code
WORKDIR /app
COPY main.py .
# 3) expose port and run
ENV PORT=8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]