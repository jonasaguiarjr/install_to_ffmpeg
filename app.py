import os
import subprocess
import boto3
import uuid
import shutil
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- CONFIGURAÇÃO MINIO ---
s3 = boto3.client('s3',
    endpoint_url=os.environ.get('S3_ENDPOINT'),
    aws_access_key_id=os.environ.get('S3_ACCESS_KEY'),
    aws_secret_access_key=os.environ.get('S3_SECRET_KEY'),
    region_name='us-east-1'
)

def get_audio_duration(file_path):
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    return float(subprocess.check_output(cmd).decode('utf-8').strip())

@app.route('/processar-audio', methods=['POST'])
def processar_audio():
    # ... (código igual, foco no endpoint de vídeo abaixo) ...
    # Para economizar espaço, mantenha a rota de áudio como estava ou copie do anterior.
    return jsonify({"status": "ok"}), 200 

@app.route('/criar-video', methods=['POST'])
def criar_video():
    data = request.json
    bucket_in = data.get('bucket_in')
    audio_key = data.get('audio_key')
    images_list = data.get('images_list', [])
    bucket_out = data.get('bucket_out')

    unique_id = str(uuid.uuid4())
    work_dir = f"/tmp/{unique_id}"
    os.makedirs(work_dir, exist_ok=True)
    local_audio = f"{work_dir}/audio.mp3"
    
    try:
        # --- DIAGNÓSTICO PRECISO ---
        
        # 1. Tenta baixar o Áudio
        print(f"[{unique_id}] Baixando áudio: {audio_key}")
        try:
            s3.download_file(bucket_in, audio_key, local_audio)
        except Exception as e:
            raise Exception(f"ERRO DE ARQUIVO: Não achei o áudio '{audio_key}' no bucket '{bucket_in}'.")

        # 2. Tenta baixar as Imagens uma por uma
        print(f"[{unique_id}] Baixando {len(images_list)} imagens...")
        first_ext = "jpg"
        
        for index, img_key in enumerate(images_list):
            try:
                ext = img_key.split('.')[-1]
                if index == 0: first_ext = ext
                local_img_name = f"{work_dir}/img_{index:03d}.{ext}"
                
                s3.download_file(bucket_in, img_key, local_img_name)
            except Exception as e:
                # AQUI ESTÁ O SEGREDO: Ele vai te contar qual imagem falhou
                raise Exception(f"ERRO DE ARQUIVO: Não achei a imagem '{img_key}' (índice {index+1}) no bucket '{bucket_in}'. Verifique se o loop no n8n gerou os arquivos corretamente.")

        # 3. Renderização (Se chegou aqui, os arquivos existem)
        audio_duration = get_audio_duration(local_audio)
        framerate = len(images_list) / audio_duration
        local_video_out = f"{work_dir}/video_final.mp4"

        print(f"[{unique_id}] Renderizando...")
        
        # Monta comando do FFmpeg
        command = [
            'ffmpeg', '-y',
            '-framerate', str(framerate),
            '-i', f"{work_dir}/img_%03d.{first_ext}", 
            '-i', local_audio,
            '-c:v', 'libx264', '-r', '30', '-pix_fmt', 'yuv420p',
            '-shortest',
            local_video_out
        ]
        
        if len(images_list) == 1:
            command = [
                'ffmpeg', '-y', '-loop', '1',
                '-i', f"{work_dir}/img_000.{first_ext}",
                '-i', local_audio,
                '-c:v', 'libx264', '-tune', 'stillimage',
                '-c:a', 'aac', '-b:a', '192k', '-pix_fmt', 'yuv420p',
                '-shortest', local_video_out
            ]

        subprocess.run(command, check=True)

        output_key = f"video_{unique_id}.mp4"
        s3.upload_file(local_video_out, bucket_out, output_key)

        return jsonify({"status": "sucesso", "file": output_key}), 200

    except Exception as e:
        # Retorna o erro limpo para o n8n
        return jsonify({"erro": str(e)}), 500
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
