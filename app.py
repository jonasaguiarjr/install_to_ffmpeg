import os
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

def get_audio_duration(file_path):
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    return float(subprocess.check_output(cmd).decode('utf-8').strip())

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
        # 1. Download Áudio
        print(f"[{unique_id}] Baixando áudio...")
        s3.download_file(bucket_in, audio_key, local_audio)

        # 2. Download Imagens e Preparação
        print(f"[{unique_id}] Baixando {len(images_list)} imagens...")
        local_images = []
        
        # Padroniza extensão para evitar erros
        master_ext = "jpg" 
        
        for index, img_key in enumerate(images_list):
            local_name = f"img_{index:03d}.{master_ext}"
            local_path = f"{work_dir}/{local_name}"
            try:
                s3.download_file(bucket_in, img_key, local_path)
                local_images.append(local_name)
            except Exception:
                raise Exception(f"ERRO: Imagem '{img_key}' não encontrada no bucket.")

        # 3. Cálculo do Tempo (A Lógica do seu JavaScript traduzida)
        audio_duration = get_audio_duration(local_audio)
        total_imagens = len(local_images)
        
        # Tempo que cada imagem deve ficar na tela
        tempo_por_imagem = audio_duration / total_imagens

        # 4. Criar arquivo 'input.txt' para o Concat Demuxer
        # Isso substitui o loop complexo do JavaScript por uma lista limpa
        concat_file_path = f"{work_dir}/inputs.txt"
        with open(concat_file_path, 'w') as f:
            for img_name in local_images:
                f.write(f"file '{img_name}'\n")
                f.write(f"duration {tempo_por_imagem:.4f}\n")
            
            # Repete a última imagem para evitar corte brusco no final
            f.write(f"file '{local_images[-1]}'\n")

        # 5. Renderização FFmpeg
        local_video_out = f"{work_dir}/video_final.mp4"
        print(f"[{unique_id}] Renderizando: {total_imagens} imgs, {tempo_por_imagem:.2f}s cada...")
        
        # Comando Otimizado
        command = [
            'ffmpeg', '-y',
            '-f', 'concat',       # Usa o arquivo de lista que criamos
            '-safe', '0',
            '-i', concat_file_path,
            '-i', local_audio,
            '-c:v', 'libx264',
            '-r', '30',           # 30 FPS fixo
            '-pix_fmt', 'yuv420p',
            '-shortest',          # Garante que acaba junto com o áudio
            local_video_out
        ]

        # Tratamento especial se for apenas 1 imagem (Vídeo Estático)
        if total_imagens == 1:
            command = [
                'ffmpeg', '-y', '-loop', '1',
                '-i', f"{work_dir}/{local_images[0]}",
                '-i', local_audio,
                '-c:v', 'libx264', '-tune', 'stillimage',
                '-c:a', 'aac', '-b:a', '192k', '-pix_fmt', 'yuv420p',
                '-shortest', local_video_out
            ]

        subprocess.run(command, check=True)

        output_key = f"video_{unique_id}.mp4"
        s3.upload_file(local_video_out, bucket_out, output_key)

        return jsonify({
            "status": "sucesso", 
            "file": output_key,
            "duration_used": audio_duration,
            "images_count": total_imagens
        }), 200

    except Exception as e:
        print(f"ERRO: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
