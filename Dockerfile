# Use a versão oficial mais recente
FROM n8nio/n8n:latest

# Troca para root para poder instalar pacotes
USER root

# Instala o ffmpeg (o n8n oficial usa base Alpine Linux)
# O flag --no-cache mantém a imagem leve
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
    
# Retorna para o usuário 'node' (obrigatório para segurança e funcionamento do n8n)
USER node
