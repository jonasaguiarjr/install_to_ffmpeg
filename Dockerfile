# Imagem base leve com suporte a apt
FROM node:18-slim

# Root para instalar dependências
USER root

# Instala ffmpeg e dependências úteis
RUN apt-get update && \
    apt-get install -y ffmpeg curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
    
# Cria diretório
WORKDIR /app

# Copia o código
COPY server.js .

# Instala dependência
RUN npm install express

# Expõe a porta
EXPOSE 3000

# Comando para manter o container rodando
CMD ["sleep", "infinity", "node", "server.js"]
