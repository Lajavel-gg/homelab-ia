# manga-image-translator pour le DGX Spark (ARM64 + GPU) : l'image officielle est faite pour les PC x86.
# Version figée : le même commit que celui testé avec l'outil Manga (mettre à jour volontairement).
FROM nvcr.io/nvidia/pytorch:26.08-py3
ARG MIT_COMMIT=d6de6ab62a7be6afc0d07a3184149cdefbcd32f2

RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg libsm6 libxext6 libvulkan1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN git init -q . && git remote add origin https://github.com/zyddnys/manga-image-translator.git \
    && git fetch -q --depth 1 origin "$MIT_COMMIT" && git checkout -q FETCH_HEAD && rm -rf .git

# torch vient de l'image NVIDIA (compilée pour le GB10) ; le module Rust est installé à part car il n'a peut-être
# pas de version ARM : il ne sert qu'au détecteur « paddle », que l'on n'utilise pas.
RUN grep -v -E '^(torch|torchvision|torchaudio)([<>=~ ]|$)|^rusty-manga-image-translator|^--extra-index-url' requirements.txt \
        > /tmp/req.txt \
    && pip install --no-cache-dir -r /tmp/req.txt \
    && (pip install --no-cache-dir rusty-manga-image-translator==0.12.1 \
          --extra-index-url https://frederik-uni.github.io/manga-image-translator-rust/python/wheels/simple/ \
        || echo "rusty-manga-image-translator indisponible pour cette plateforme : détecteur paddle désactivé")
COPY paddle_facultatif.py fr_post_dict.txt /app/
RUN python paddle_facultatif.py && python -c "import manga_translator.detection"

ENV PYTHONPATH=/app
# Modèles (OCR, détection, effacement) téléchargés au premier usage dans le volume /app/models
ENTRYPOINT ["python"]
CMD ["server/main.py", "--host", "127.0.0.1", "--port", "5003", "--use-gpu", "--start-instance", \
     "--models-ttl", "60", "--post-dict", "/app/fr_post_dict.txt"]
