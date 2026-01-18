import os
import subprocess
import boto3
from flask import Flask, request, jsonify

app = Flask(__name__)

# Configurações do MinIO (pegas das variáveis de ambiente)
s3 = boto3.client('s3',
    endpoint_url=os.environ.get('S3_ENDPOINT'), # ex: http://minio:9000
    aws_access_key_id=os.environ.get('S3_ACCESS_KEY'),
    aws_secret_access_key=os.environ.get('S3_SECRET_KEY'),
    region_name='us-east-1' # Padrão MinIO
)

@app.route('/processar-audio', methods=['POST'])
def processar():
    data = request.json
    bucket_in = data.get('bucket_in')
    file_key = data.get('file_key') # Nome do arquivo (ex: audio.mp3)
    bucket_out = data.get('bucket_out')
    
    local_input = f"/tmp/{file_key}"
    local_output = f"/tmp/processed_{file_key}"

    try:
        # 1. Baixar do MinIO
        print(f"Baixando {file_key}...")
        s3.download_file(bucket_in, file_key, local_input)

        # 2. Rodar FFmpeg (Exemplo: Converter para WAV 16khz mono)
        # Ajuste o comando ffmpeg conforme sua necessidade
        command = [
            'ffmpeg', '-y', 
            '-i', local_input, 
            '-ar', '16000', '-ac', '1', 
            local_output
        ]
        subprocess.run(command, check=True)

        # 3. Upload para o MinIO
        output_key = f"processed_{file_key}"
        print(f"Subindo {output_key}...")
        s3.upload_file(local_output, bucket_out, output_key)

        # Limpeza
        os.remove(local_input)
        os.remove(local_output)

        return jsonify({"status": "sucesso", "file": output_key, "bucket": bucket_out}), 200

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
