import os
import glob
import subprocess
import boto3
import uuid
import shutil
from flask import Flask, request, jsonify

app = Flask(__name__)

# Configurações MinIO
s3 = boto3.client('s3',
    endpoint_url=os.environ.get('S3_ENDPOINT'),
    aws_access_key_id=os.environ.get('S3_ACCESS_KEY'),
    aws_secret_access_key=os.environ.get('S3_SECRET_KEY'),
    region_name='us-east-1'
)

@app.route('/criar-video', methods=['POST'])
def criar_video():
    # Esperamos receber:
    # {
    #   "bucket_in": "ffmpeg",
    #   "audio_key": "narracao.mp3",
    #   "images_list": ["img1.jpg", "img2.jpg", "img3.jpg"],
    #   "bucket_out": "ffmpeg"
    # }
    data = request.json
    bucket_in = data.get('bucket_in')
    audio_key = data.get('audio_key')
    images_list = data.get('images_list', [])
    bucket_out = data.get('bucket_out')

    if not images_list:
        return jsonify({"erro": "Lista de imagens vazia"}), 400

    unique_id = str(uuid.uuid4())
    work_dir = f"/tmp/{unique_id}"
    os.makedirs(work_dir, exist_ok=True)

    local_audio = f"{work_dir}/audio_input.mp3"
    local_video_out = f"{work_dir}/video_final.mp4"

    try:
        # 1. Baixar Áudio
        print(f"[{unique_id}] Baixando áudio: {audio_key}")
        s3.download_file(bucket_in, audio_key, local_audio)

        # 2. Baixar Imagens e Renomear (001.jpg, 002.jpg...)
        print(f"[{unique_id}] Baixando {len(images_list)} imagens...")
        for index, img_key in enumerate(images_list):
            # Extensão do arquivo original
            ext = img_key.split('.')[-1]
            # Nome sequencial obrigatório para o FFmpeg (000.jpg, 001.jpg)
            local_img_name = f"{work_dir}/img_{index:03d}.{ext}"
            s3.download_file(bucket_in, img_key, local_img_name)

        # 3. Calcular duração do áudio (para o vídeo ter o mesmo tamanho)
        probe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', local_audio]
        audio_duration = float(subprocess.check_output(probe_cmd).decode('utf-8').strip())
        
        # Tempo por imagem (distribui as imagens pelo tempo do áudio)
        # Ex: Se áudio tem 10s e temos 5 imagens = 2s por imagem
        framerate = len(images_list) / audio_duration

        # 4. Comando FFmpeg para criar Slideshow
        # -framerate: define quantas imagens por segundo (inverso do tempo por imagem)
        # -i img_%03d.jpg: padrão de arquivo sequencial
        # -c:v libx264: codec de vídeo compatível
        # -pix_fmt yuv420p: garante compatibilidade com players (Quicktime/Windows)
        # -shortest: termina o vídeo quando o menor input (áudio ou imagens) acabar
        print(f"[{unique_id}] Renderizando vídeo...")
        
        command = [
            'ffmpeg', '-y',
            '-framerate', str(framerate),
            '-i', f"{work_dir}/img_%03d.jpg",  # Input Imagens
            '-i', local_audio,                 # Input Áudio
            '-c:v', 'libx264',
            '-r', '30',                        # FPS de saída do vídeo (fluidez)
            '-pix_fmt', 'yuv420p',
            '-shortest',                       # Corta se sobrar imagem
            local_video_out
        ]

        # Se tiver só 1 imagem, usamos 'loop'
        if len(images_list) == 1:
            command = [
                'ffmpeg', '-y',
                '-loop', '1',
                '-i', f"{work_dir}/img_000.jpg",
                '-i', local_audio,
                '-c:v', 'libx264',
                '-tune', 'stillimage',
                '-c:a', 'aac',
                '-b:a', '192k',
                '-pix_fmt', 'yuv420p',
                '-shortest',
                local_video_out
            ]

        subprocess.run(command, check=True)

        # 5. Upload
        output_key = f"video_{unique_id}.mp4"
        print(f"[{unique_id}] Subindo {output_key}...")
        s3.upload_file(local_video_out, bucket_out, output_key)

        return jsonify({
            "status": "sucesso",
            "file": output_key,
            "bucket": bucket_out
        }), 200

    except Exception as e:
        print(f"ERRO: {e}")
        return jsonify({"erro": str(e)}), 500
        
    finally:
        # Limpa a pasta inteira do job
        shutil.rmtree(work_dir, ignore_errors=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
