FROM python:3.9-slim

# 1. Instala o FFmpeg e dependências do sistema
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# 2. Define diretório de trabalho
WORKDIR /app

# 3. Instala bibliotecas Python para API e conexão S3 (MinIO)
RUN pip install flask boto3

# 4. Copia o código da aplicação
COPY app.py .

# 5. Expõe a porta 5000 e roda o servidor
EXPOSE 5000
CMD ["python", "app.py"]
