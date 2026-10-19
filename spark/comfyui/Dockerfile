FROM nvcr.io/nvidia/pytorch:26.08-py3
WORKDIR /opt
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git \
 && cd ComfyUI && grep -v -E '^(torch|torchvision|torchaudio)([<>=~ ]|$)' requirements.txt > req.txt \
 && pip install --no-cache-dir -r req.txt
WORKDIR /opt/ComfyUI
EXPOSE 8188
CMD ["python", "main.py", "--listen", "0.0.0.0", "--port", "8188"]
