import os
import subprocess
import boto3
import uuid
from flask import Flask, request, jsonify

app = Flask(__name__)

# Configurações do MinIO
s3 = boto3.client('s3',
    endpoint_url=os.environ.get('S3_ENDPOINT'),
    aws_access_key_id=os.environ.get('S3_ACCESS_KEY'),
    aws_secret_access_key=os.environ.get('S3_SECRET_KEY'),
    region_name='us-east-1'
)

@app.route('/processar-audio', methods=['POST'])
def processar():
    data = request.json
    bucket_in = data.get('bucket_in')
    file_key = data.get('file_key')
    bucket_out = data.get('bucket_out')

    # --- MUDANÇA CRÍTICA AQUI ---
    # Geramos um ID único para esta execução específica
    unique_id = str(uuid.uuid4())
    
    # O arquivo no disco terá esse ID no nome para não colidir com outros
    safe_filename = file_key.replace('/', '_') # Remove barras para evitar erros de pasta
    local_input = f"/tmp/{unique_id}_{safe_filename}"
    local_output = f"/tmp/{unique_id}_processed_{safe_filename}"

    try:
        # 1. Download
        print(f"[{unique_id}] Baixando {file_key}...")
        s3.download_file(bucket_in, file_key, local_input)

        if os.path.getsize(local_input) == 0:
            raise Exception("Arquivo vazio (0 bytes).")

        # 2. FFmpeg
        print(f"[{unique_id}] Convertendo...")
        command = [
            'ffmpeg', '-y', 
            '-i', local_input, 
            '-ar', '16000', '-ac', '1', 
            local_output
        ]
        
        result = subprocess.run(command, capture_output=True, text=True)

        if result.returncode != 0:
            raise Exception(f"Erro FFmpeg: {result.stderr}")

        if not os.path.exists(local_output):
            raise Exception(f"Arquivo de saída não gerado. Log: {result.stderr}")

        # 3. Upload
        # O nome no Bucket continua sendo o bonito (processed_narracao.mp3)
        # Só o nome temporário local que é "feio" (uuid_processed...)
        final_output_key = f"processed_{file_key}"
        
        print(f"[{unique_id}] Subindo {final_output_key}...")
        s3.upload_file(local_output, bucket_out, final_output_key)

        return jsonify({
            "status": "sucesso", 
            "file": final_output_key, 
            "bucket": bucket_out
        }), 200

    except Exception as e:
        print(f"ERRO: {str(e)}")
        return jsonify({"erro": str(e)}), 500

    finally:
        # Limpeza segura: apaga apenas os arquivos DESTA execução (pelo ID)
        if os.path.exists(local_input):
            os.remove(local_input)
        if os.path.exists(local_output):
            os.remove(local_output)

if __name__ == '__main__':
    # Threaded=True ajuda a lidar com múltiplas requisições simultâneas
    app.run(host='0.0.0.0', port=5000, threaded=True)
