# Imagem base leve com suporte a apt
FROM node:18-slim

# Root para instalar dependências
USER root

# Instala ffmpeg e dependências úteis
RUN apt-get update && \
    apt-get install -y ffmpeg curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Comando para manter o container rodando
CMD ["sleep", "infinity"]
