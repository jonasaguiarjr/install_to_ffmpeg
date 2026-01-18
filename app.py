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

# --- FUNÇÕES AUXILIARES ---
def get_audio_duration(file_path):
    """Extrai a duração exata do áudio em segundos usando ffprobe"""
    cmd = [
        'ffprobe', 
        '-v', 'error', 
        '-show_entries', 'format=duration', 
        '-of', 'default=noprint_wrappers=1:nokey=1', 
        file_path
    ]
    return float(subprocess.check_output(cmd).decode('utf-8').strip())

# --- ROTA 1: PROCESSAR ÁUDIO & PEGAR DURAÇÃO ---
@app.route('/processar-audio', methods=['POST'])
def processar_audio():
    data = request.json
    bucket_in = data.get('bucket_in')
    file_key = data.get('file_key')
    bucket_out = data.get('bucket_out')

    unique_id = str(uuid.uuid4())
    work_dir = f"/tmp/{unique_id}"
    os.makedirs(work_dir, exist_ok=True)
    
    local_input = f"{work_dir}/input_{file_key.replace('/', '_')}"
    # Se quiser salvar processado, descomente abaixo. 
    # Por enquanto, vamos focar em retornar a duração.
    # local_output = f"{work_dir}/processed.mp3" 

    try:
        print(f"[{unique_id}] Baixando áudio para análise: {file_key}")
        s3.download_file(bucket_in, file_key, local_input)

        if os.path.getsize(local_input) == 0:
            raise Exception("Arquivo de áudio vazio.")

        # Pega a duração
        duration = get_audio_duration(local_input)
        print(f"[{unique_id}] Duração detectada: {duration}s")

        # Se você precisar converter o áudio, faça aqui.
        # Se for apenas para pegar dados, não precisamos rodar o ffmpeg pesado.

        return jsonify({
            "status": "sucesso", 
            "file": file_key, 
            "bucket": bucket_out,
            "duration_seconds": duration,
            "duration_formatted": f"{duration:.2f}s"
        }), 200

    except Exception as e:
        print(f"ERRO AUDIO: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# --- ROTA 2: CRIAR VÍDEO (SLIDESHOW) ---
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
    local_video_out = f"{work_dir}/video_final.mp4"

    try:
        # 1. Download Áudio
        print(f"[{unique_id}] Baixando áudio: {audio_key}")
        s3.download_file(bucket_in, audio_key, local_audio)

        # 2. Download Imagens
        print(f"[{unique_id}] Baixando {len(images_list)} imagens...")
        first_ext = "jpg" # Default
        
        for index, img_key in enumerate(images_list):
            ext = img_key.split('.')[-1]
            if index == 0: first_ext = ext
            
            # Salva como img_000.jpg, img_001.jpg, etc.
            local_img_name = f"{work_dir}/img_{index:03d}.{ext}"
            s3.download_file(bucket_in, img_key, local_img_name)

        # 3. Calcula Framerate
        audio_duration = get_audio_duration(local_audio)
        framerate = len(images_list) / audio_duration
        
        # 4. Renderiza
        print(f"[{unique_id}] Renderizando vídeo ({framerate:.2f} fps)...")
        
        # Comando padrão para múltiplas imagens
        command = [
            'ffmpeg', '-y',
            '-framerate', str(framerate),
            '-i', f"{work_dir}/img_%03d.{first_ext}", 
            '-i', local_audio,
            '-c:v', 'libx264', '-r', '30', '-pix_fmt', 'yuv420p',
            '-shortest',
            local_video_out
        ]

        # Comando especial para imagem única (Loop)
        if len(images_list) == 1:
            command = [
                'ffmpeg', '-y',
                '-loop', '1',
                '-i', f"{work_dir}/img_000.{first_ext}",
                '-i', local_audio,
                '-c:v', 'libx264', '-tune', 'stillimage',
                '-c:a', 'aac', '-b:a', '192k', '-pix_fmt', 'yuv420p',
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
        print(f"ERRO VIDEO: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
