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

    if not images_list:
        return jsonify({"erro": "Lista de imagens vazia"}), 400

    unique_id = str(uuid.uuid4())
    work_dir = f"/tmp/{unique_id}"
    os.makedirs(work_dir, exist_ok=True)
    local_audio = f"{work_dir}/audio.mp3"
    
    try:
        # 1. Download do Áudio
        print(f"[{unique_id}] Baixando áudio...")
        try:
            s3.download_file(bucket_in, audio_key, local_audio)
        except Exception:
            raise Exception(f"ERRO: Áudio '{audio_key}' não encontrado.")

        # 2. Download das Imagens (COM CORREÇÃO DE EXTENSÃO)
        print(f"[{unique_id}] Baixando {len(images_list)} imagens...")
        
        # Pega a extensão da primeira imagem para ser o "Mestre" (ex: jpg)
        master_ext = images_list[0].split('.')[-1]
        
        for index, img_key in enumerate(images_list):
            # MUDANÇA AQUI:
            # Não usamos mais a extensão do arquivo original para salvar no disco.
            # Forçamos todos a terem a 'master_ext' para o padrão do FFmpeg não quebrar.
            local_img_name = f"{work_dir}/img_{index:03d}.{master_ext}"
            
            try:
                s3.download_file(bucket_in, img_key, local_img_name)
            except Exception:
                raise Exception(f"ERRO: Imagem '{img_key}' (índice {index+1}) não encontrada.")

        # 3. Renderização
        audio_duration = get_audio_duration(local_audio)
        framerate = len(images_list) / audio_duration
        local_video_out = f"{work_dir}/video_final.mp4"

        print(f"[{unique_id}] Renderizando com framerate {framerate:.4f}...")
        
        command = [
            'ffmpeg', '-y',
            '-framerate', str(framerate),
            # Agora garantimos que todas as imagens no disco terminam com .{master_ext}
            '-i', f"{work_dir}/img_%03d.{master_ext}", 
            '-i', local_audio,
            '-c:v', 'libx264', '-r', '30', '-pix_fmt', 'yuv420p',
            '-shortest',
            local_video_out
        ]
        
        # Lógica para 1 imagem (Vídeo Estático)
        if len(images_list) == 1:
            command = [
                'ffmpeg', '-y', '-loop', '1',
                '-i', f"{work_dir}/img_000.{master_ext}",
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
        return jsonify({"erro": str(e)}), 500
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
