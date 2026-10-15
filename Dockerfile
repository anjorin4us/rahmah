# For running on an IIUM or private server: docker build -t rahmah . && docker run -p 8000:8000 --env-file .env rahmah
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY rahmah ./rahmah
COPY static ./static
COPY content ./content
RUN useradd -m rahmah && chown -R rahmah /app
USER rahmah
EXPOSE 8000
CMD ["uvicorn", "rahmah.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers"]
