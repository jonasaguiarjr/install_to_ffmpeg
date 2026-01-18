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
    """Obtém a duração exata do arquivo de áudio em segundos."""
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    return float(subprocess.check_output(cmd).decode('utf-8').strip())

@app.route('/processar-audio', methods=['POST'])
def processar_audio():
    """Rota auxiliar para checagem ou processamento simples de áudio."""
    try:
        data = request.json
        bucket_in = data.get('bucket_in')
        file_key = data.get('file_key')
        
        # Lógica simples para retornar sucesso e confirmar que o servidor está vivo
        # Se precisar recuperar a duração aqui também, podemos implementar.
        return jsonify({"status": "sucesso", "message": "Endpoint de áudio ativo"}), 200
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route('/criar-video', methods=['POST'])
def criar_video():
    """Gera um vídeo slideshow sincronizado usando Concat Demuxer."""
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
        print(f"[{unique_id}] Baixando áudio: {audio_key}")
        try:
            s3.download_file(bucket_in, audio_key, local_audio)
        except Exception:
            raise Exception(f"ERRO: Áudio '{audio_key}' não encontrado no bucket.")

        # 2. Download das Imagens e Padronização
        print(f"[{unique_id}] Baixando {len(images_list)} imagens...")
        local_images = []
        
        # Forçamos todos os arquivos locais a serem .jpg para o FFmpeg não reclamar
        master_ext = "jpg" 
        
        for index, img_key in enumerate(images_list):
            local_name = f"img_{index:03d}.{master_ext}"
            local_path = f"{work_dir}/{local_name}"
            try:
                s3.download_file(bucket_in, img_key, local_path)
                local_images.append(local_name)
            except Exception:
                raise Exception(f"ERRO: Imagem '{img_key}' (índice {index+1}) não encontrada no bucket.")

        # 3. Cálculo do Tempo (Divisão igualitária do tempo do áudio)
        audio_duration = get_audio_duration(local_audio)
        total_imagens = len(local_images)
        
        # Tempo exato que cada imagem deve ficar na tela
        tempo_por_imagem = audio_duration / total_imagens

        # 4. Criar arquivo 'inputs.txt' (Concat Demuxer)
        # Esse método garante que cada imagem apareça pelo tempo exato calculado
        concat_file_path = f"{work_dir}/inputs.txt"
        with open(concat_file_path, 'w') as f:
            for img_name in local_images:
                f.write(f"file '{img_name}'\n")
                f.write(f"duration {tempo_por_imagem:.4f}\n")
            
            # Repete a última imagem sem duração para evitar "glitch" no final
            # (O FFmpeg precisa disso para fechar o stream de vídeo corretamente)
            f.write(f"file '{local_images[-1]}'\n")

        # 5. Renderização FFmpeg
        local_video_out = f"{work_dir}/video_final.mp4"
        print(f"[{unique_id}] Renderizando: {total_imagens} imagens, {tempo_por_imagem:.2f}s cada...")
        
        # Comando para Slideshow Dinâmico
        command = [
            'ffmpeg', '-y',
            '-f', 'concat',       # Usa o arquivo de lista como input
            '-safe', '0',         # Permite ler caminhos de arquivos
            '-i', concat_file_path,
            '-i', local_audio,
            '-c:v', 'libx264',
            '-r', '30',           # 30 FPS fixo
            '-pix_fmt', 'yuv420p',
            '-shortest',          # Corta o vídeo quando o áudio acabar
            local_video_out
        ]

        # Tratamento especial OTIMIZADO para apenas 1 imagem (Vídeo Estático)
        if total_imagens == 1:
            print(f"[{unique_id}] Modo imagem única detectado.")
            command = [
                'ffmpeg', '-y', '-loop', '1',
                '-i', f"{work_dir}/{local_images[0]}",
                '-i', local_audio,
                '-c:v', 'libx264', '-tune', 'stillimage',
                '-c:a', 'aac', '-b:a', '192k', '-pix_fmt', 'yuv420p',
                '-shortest', local_video_out
            ]

        subprocess.run(command, check=True)

        # 6. Upload
        output_key = f"video_{unique_id}.mp4"
        print(f"[{unique_id}] Subindo {output_key}...")
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
        # Limpeza da pasta temporária
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
