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

# --- ROTA 1: PROCESSAR ÁUDIO (Restaurada) ---
@app.route('/processar-audio', methods=['POST'])
def processar_audio():
    """Baixa o áudio e retorna sua duração exata."""
    data = request.json
    bucket_in = data.get('bucket_in')
    file_key = data.get('file_key')
    bucket_out = data.get('bucket_out')

    unique_id = str(uuid.uuid4())
    work_dir = f"/tmp/{unique_id}"
    os.makedirs(work_dir, exist_ok=True)
    local_input = f"{work_dir}/audio_temp.mp3"

    try:
        print(f"[{unique_id}] Analisando áudio: {file_key}")
        
        # 1. Download
        s3.download_file(bucket_in, file_key, local_input)

        if os.path.getsize(local_input) == 0:
            raise Exception("Arquivo de áudio vazio.")

        # 2. Pega a duração
        duration = get_audio_duration(local_input)
        print(f"[{unique_id}] Duração detectada: {duration}s")

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

# --- ROTA 2: CRIAR VÍDEO (Slideshow Sincronizado) ---
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
        master_ext = "jpg" 
        
        for index, img_key in enumerate(images_list):
            local_name = f"img_{index:03d}.{master_ext}"
            local_path = f"{work_dir}/{local_name}"
            try:
                s3.download_file(bucket_in, img_key, local_path)
                local_images.append(local_name)
            except Exception:
                raise Exception(f"ERRO: Imagem '{img_key}' (índice {index+1}) não encontrada no bucket.")

        # 3. Cálculo do Tempo
        audio_duration = get_audio_duration(local_audio)
        total_imagens = len(local_images)
        tempo_por_imagem = audio_duration / total_imagens

        # 4. Criar arquivo 'inputs.txt' (Concat Demuxer)
        concat_file_path = f"{work_dir}/inputs.txt"
        with open(concat_file_path, 'w') as f:
            for img_name in local_images:
                f.write(f"file '{img_name}'\n")
                f.write(f"duration {tempo_por_imagem:.4f}\n")
            f.write(f"file '{local_images[-1]}'\n")

        # 5. Renderização FFmpeg
        local_video_out = f"{work_dir}/video_final.mp4"
        print(f"[{unique_id}] Renderizando: {total_imagens} imagens, {tempo_por_imagem:.2f}s cada...")
        
        command = [
            'ffmpeg', '-y',
            '-f', 'concat', '-safe', '0',
            '-i', concat_file_path,
            '-i', local_audio,
            '-c:v', 'libx264', '-r', '30', '-pix_fmt', 'yuv420p',
            '-shortest',
            local_video_out
        ]

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
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
            
@app.route('/queimar-legenda', methods=['POST'])
def queimar_legenda():
    """Baixa vídeo e .ass, queima a legenda e sobe o vídeo final."""
    data = request.json
    bucket_in = data.get('bucket_in')
    video_key = data.get('video_key')       # ex: video_sem_legenda.mp4
    subtitle_key = data.get('subtitle_key') # ex: legenda.ass
    bucket_out = data.get('bucket_out')

    unique_id = str(uuid.uuid4())
    work_dir = f"/tmp/{unique_id}"
    os.makedirs(work_dir, exist_ok=True)

    local_video = f"{work_dir}/input_video.mp4"
    local_sub = f"{work_dir}/legenda.ass"
    local_output = f"{work_dir}/video_com_legenda.mp4"

    try:
        # 1. Download dos Arquivos
        print(f"[{unique_id}] Baixando vídeo: {video_key}")
        try:
            s3.download_file(bucket_in, video_key, local_video)
        except Exception:
            raise Exception(f"Vídeo '{video_key}' não encontrado.")

        print(f"[{unique_id}] Baixando legenda: {subtitle_key}")
        try:
            s3.download_file(bucket_in, subtitle_key, local_sub)
        except Exception:
            raise Exception(f"Legenda '{subtitle_key}' não encontrada.")

        # 2. Processamento FFmpeg (Burn)
        print(f"[{unique_id}] Queimando legendas...")
        
        # O filtro 'ass' exige o caminho completo do arquivo
        # Usamos -c:a copy para não perder tempo reprocessando o áudio (que já está bom)
        # Usamos -c:v libx264 para re-encodar o vídeo com os pixels da legenda
        command = [
            'ffmpeg', '-y',
            '-i', local_video,
            '-vf', f"ass={local_sub}",
            '-c:v', 'libx264',
            '-preset', 'ultrafast',  # Mais rápido (use 'medium' para menor arquivo)
            '-c:a', 'copy',          # Copia o áudio sem mexer (rápido)
            local_output
        ]

        result = subprocess.run(command, capture_output=True, text=True)
        
        if result.returncode != 0:
            # Se der erro, mostramos o log do FFmpeg para debug
            raise Exception(f"Erro FFmpeg: {result.stderr}")

        # 3. Upload
        output_key = f"final_com_legenda_{unique_id}.mp4"
        print(f"[{unique_id}] Subindo {output_key}...")
        s3.upload_file(local_output, bucket_out, output_key)

        return jsonify({
            "status": "sucesso",
            "file": output_key,
            "bucket": bucket_out
        }), 200

    except Exception as e:
        print(f"ERRO LEGENDA: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

# --- ROTA 4: ADICIONAR TRILHA SONORA ---
@app.route('/adicionar-musica', methods=['POST'])
def adicionar_musica():
    """Recebe vídeo e lista de músicas, mistura com volume baixo (ducking)."""
    data = request.json
    bucket_in = data.get('bucket_in')
    video_key = data.get('video_key')       # ex: final_com_legenda.mp4
    music_list = data.get('music_list', []) # Lista: ['bg_music_0.mp3', 'bg_music_1.mp3']
    bucket_out = data.get('bucket_out')
    # Volume da música (0.1 = 10%, 0.2 = 20%). Ajuste conforme necessário.
    bg_volume = data.get('volume', 0.15) 

    if not music_list:
        return jsonify({"erro": "Lista de músicas vazia"}), 400

    unique_id = str(uuid.uuid4())
    work_dir = f"/tmp/{unique_id}"
    os.makedirs(work_dir, exist_ok=True)

    local_video = f"{work_dir}/video_input.mp4"
    local_output = f"{work_dir}/video_final_com_audio.mp4"
    concat_list_path = f"{work_dir}/music_list.txt"

    try:
        # 1. Download do Vídeo
        print(f"[{unique_id}] Baixando vídeo: {video_key}")
        s3.download_file(bucket_in, video_key, local_video)

        # 2. Download das Músicas e Criação da Playlist
        print(f"[{unique_id}] Preparando {len(music_list)} músicas...")
        with open(concat_list_path, 'w') as f:
            for i, music_key in enumerate(music_list):
                local_music = f"{work_dir}/bg_{i}.mp3"
                try:
                    s3.download_file(bucket_in, music_key, local_music)
                    f.write(f"file '{local_music}'\n")
                except Exception:
                    print(f"Aviso: Música {music_key} falhou. Ignorando.")

        # 3. Mixagem com FFmpeg
        # Lógica:
        # - Input 0: Vídeo (tem vídeo + voz)
        # - Input 1: Lista de Músicas (concat)
        # - Filter: 
        #    1. [1:a] ajusta volume da música para 15% [musica_baixa]
        #    2. [0:a][musica_baixa] mistura os dois, duração do mais curto (vídeo)
        
        print(f"[{unique_id}] Mixando áudio (Volume: {bg_volume})...")
        
        command = [
            'ffmpeg', '-y',
            '-i', local_video,
            '-f', 'concat', '-safe', '0', '-i', concat_list_path,
            '-filter_complex', 
            f"[1:a]volume={bg_volume}[bg];[0:a][bg]amix=inputs=2:duration=first[a_out]",
            '-map', '0:v',      # Usa o vídeo do input 0
            '-map', '[a_out]',  # Usa o áudio mixado
            '-c:v', 'copy',     # Não re-encoda o vídeo (Super Rápido!)
            '-c:a', 'aac', '-b:a', '192k',
            local_output
        ]

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Erro FFmpeg: {result.stderr}")

        # 4. Upload
        output_key = f"final_ready_{unique_id}.mp4"
        print(f"[{unique_id}] Subindo {output_key}...")
        s3.upload_file(local_output, bucket_out, output_key)

        return jsonify({
            "status": "sucesso",
            "file": output_key,
            "bucket": bucket_out
        }), 200

    except Exception as e:
        print(f"ERRO MUSIC: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
