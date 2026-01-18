import os
import subprocess
import boto3
import uuid
import shutil
from flask import Flask, request, jsonify
import textwrap

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

# --- ROTA 4: MIXAGEM FINAL (COM PRESERVAÇÃO DE CANAIS) ---
@app.route('/adicionar-musica', methods=['POST'])
def adicionar_musica():
    data = request.json
    bucket_in = data.get('bucket_in')
    video_key = data.get('video_key')       # Vídeo que já tem legenda e narração
    music_list = data.get('music_list', []) 
    bucket_out = data.get('bucket_out')
    
    # Volume padrão 0.15 (15%) se não for informado
    bg_volume = data.get('volume', 0.15)

    if not music_list:
        return jsonify({"erro": "Lista de músicas vazia."}), 400

    unique_id = str(uuid.uuid4())
    work_dir = f"/tmp/{unique_id}"
    os.makedirs(work_dir, exist_ok=True)

    local_video = f"{work_dir}/video_input.mp4"
    local_output = f"{work_dir}/video_final_pronto.mp4"
    concat_list_path = f"{work_dir}/music_list.txt"

    try:
        # 1. Downloads
        print(f"[{unique_id}] Baixando vídeo: {video_key}")
        try:
            s3.download_file(bucket_in, video_key, local_video)
        except Exception:
            raise Exception(f"ERRO: Vídeo '{video_key}' não encontrado.")

        print(f"[{unique_id}] Baixando {len(music_list)} músicas...")
        with open(concat_list_path, 'w') as f:
            for i, music_key in enumerate(music_list):
                local_music = f"{work_dir}/bg_{i}.mp3"
                try:
                    s3.download_file(bucket_in, music_key, local_music)
                except Exception:
                    raise Exception(f"ERRO: Música '{music_key}' não encontrada.")
                f.write(f"file '{local_music}'\n")

        # 2. Mixagem Profissional (Stereo + Ducking)
        print(f"[{unique_id}] Mixando áudio (Vol: {bg_volume})...")
        
        # EXPLICAÇÃO DO COMANDO:
        # [0:a]aformat=channel_layouts=stereo[a_nar] -> Pega áudio do vídeo, força Stereo, chama de 'a_nar'
        # [1:a]aformat=channel_layouts=stereo,volume={bg_volume}[a_mus] -> Pega música, força Stereo, baixa volume, chama de 'a_mus'
        # [a_nar][a_mus]amix... -> Junta os dois
        
        command = [
            'ffmpeg', '-y',
            '-i', local_video,
            '-f', 'concat', '-safe', '0', '-i', concat_list_path,
            '-filter_complex', 
            f"[0:a]aformat=channel_layouts=stereo[a_nar];[1:a]aformat=channel_layouts=stereo,volume={bg_volume}[a_mus];[a_nar][a_mus]amix=inputs=2:duration=first:dropout_transition=0[a_out]",
            '-map', '0:v',      # Mantém vídeo original
            '-map', '[a_out]',  # Usa o novo áudio mixado
            '-c:v', 'copy',     # Copia o vídeo (não perde qualidade e é rápido)
            '-c:a', 'aac', '-b:a', '192k', # Codifica o áudio final
            local_output
        ]

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Erro FFmpeg: {result.stderr}")

        # 3. Upload
        output_key = f"VIDEO_FINAL_{unique_id}.mp4"
        print(f"[{unique_id}] Subindo {output_key}...")
        s3.upload_file(local_output, bucket_out, output_key)

        return jsonify({
            "status": "sucesso",
            "file": output_key,
            "stats": {"volume_aplicado": bg_volume}
        }), 200

    except Exception as e:
        print(f"ERRO MIX: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

import textwrap # Adicione isso no topo do arquivo junto com os outros imports

# --- ROTA 5: GERAR THUMBNAIL (COM TEXTO E ARQUIVOS DO MINIO) ---
@app.route('/gerar-thumbnail', methods=['POST'])
def gerar_thumbnail():
    data = request.json
    
    # 1. Parâmetros (com valores padrão para o seu teste)
    bucket_name = data.get('bucket_in', 'ffmpeg') # Nome do bucket
    
    # Se o n8n não mandar nome, usa 'thumbnail.jpg'
    image_key = data.get('image_key', 'thumbnail.jpg') 
    
    # Nome exato da fonte no MinIO
    font_key = "montserrat-black.ttf" 
    
    text = data.get('text', 'Roteiro 303') # Texto padrão se vazio
    
    # 2. Preparação do ambiente temporário
    unique_id = str(uuid.uuid4())
    work_dir = f"/tmp/{unique_id}"
    os.makedirs(work_dir, exist_ok=True)

    local_image = f"{work_dir}/input_image.jpg"
    local_font = f"{work_dir}/font.ttf" # Nome local simplificado
    local_output = f"{work_dir}/output_thumb.jpg"

    try:
        # 3. Downloads do MinIO (A Mágica acontece aqui)
        print(f"[{unique_id}] Baixando {image_key} e {font_key} do bucket {bucket_name}...")
        
        try:
            # Baixa a imagem
            s3.download_file(bucket_name, image_key, local_image)
            # Baixa a fonte
            s3.download_file(bucket_name, font_key, local_font)
        except Exception as e:
            return jsonify({"erro": f"Falha ao baixar arquivos do MinIO: {str(e)}"}), 500

        # 4. Processamento do Texto (Quebra de linha e Cores)
        # ... (Mantendo sua lógica de texto intacta) ...
        import textwrap
        max_chars = 22
        lines = textwrap.wrap(text, width=max_chars)
        
        font_size = 70 if len(text) > 50 else 90
        line_spacing = int(font_size * 1.4)
        total_height = (len(lines) - 1) * line_spacing
        start_y = 400 - (total_height / 2)

        draw_cmds = []
        for i, line in enumerate(lines):
            safe_line = line.replace(":", "\\:").replace("'", "")
            y_pos = start_y + (i * line_spacing)
            color = "red" if i == 1 else "yellow" # Lógica de cores
            
            cmd = (
                f"drawtext=fontfile='{local_font}':text='{safe_line}':"
                f"fontcolor={color}:fontsize={font_size}:"
                f"shadowcolor=black:shadowx=5:shadowy=5:"
                f"x=(w-text_w)/2:y={y_pos}"
            )
            draw_cmds.append(cmd)

        filter_complex = ",".join(draw_cmds)

        # 5. Renderização com FFmpeg
        print(f"[{unique_id}] Renderizando...")
        command = [
            'ffmpeg', '-y',
            '-i', local_image,
            '-vf', filter_complex,
            '-q:v', '2', 
            local_output
        ]

        subprocess.run(command, check=True, capture_output=True)

        # 6. Upload do Resultado de volta para o MinIO
        output_key = f"thumb_final_{unique_id}.jpg"
        s3.upload_file(local_output, bucket_name, output_key)

        return jsonify({
            "status": "sucesso",
            "file": output_key,
            "url_minio": f"/{bucket_name}/{output_key}"
        }), 200

    except Exception as e:
        print(f"ERRO: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        # Limpeza
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)
        
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
