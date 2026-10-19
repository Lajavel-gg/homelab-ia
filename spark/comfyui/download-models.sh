#!/bin/bash
# Télécharge les modèles image (Qwen-Image 2.1 Turbo), vidéo (MiniMax H3) et 3D (Pixal3D + TRELLIS.2)
M=/opt/ai-stack/comfyui/models
HF=https://huggingface.co
get() { # dossier url
  f="$M/$1/$(basename "$2")"
  [ -s "$f" ] && [ ! -f "$f.part" ] && { echo "déjà là : $f"; return; }
  echo "→ $f"; curl -fL --retry 5 -C - -s -o "$f.part" "$2" && mv "$f.part" "$f" || echo "ÉCHEC $2"
}
# Image
get diffusion_models $HF/Comfy-Org/Qwen-Image-2.1/resolve/main/diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors &
get text_encoders    $HF/Comfy-Org/Qwen-Image-2.1/resolve/main/text_encoders/qwen3vl_8b_int8_convrot.safetensors &
get vae              $HF/Comfy-Org/Qwen-Image-2.1/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors &
get text_encoders    $HF/Comfy-Org/Qwen-Image-2.1/resolve/main/text_encoders/qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors &
wait
# 3D
get vae               $HF/Comfy-Org/Pixal3D/resolve/main/vae/trellis_2_texture_vae_bf16.safetensors &
get clip_vision       $HF/Comfy-Org/Pixal3D/resolve/main/clip_vision/dino_v3_L_naf_fp32.safetensors &
get vae               $HF/Comfy-Org/Pixal3D/resolve/main/vae/trellis_2_shape_vae_bf16.safetensors &
get geometry_estimation $HF/Comfy-Org/MoGe/resolve/main/geometry_estimation/moge_2_vitl_normal_fp16.safetensors &
get background_removal  $HF/Comfy-Org/BiRefNet/resolve/main/background_removal/birefnet.safetensors &
get diffusion_models  $HF/Comfy-Org/TRELLIS.2/resolve/main/diffusion_models/trellis_2_int8_convrot.safetensors &
get diffusion_models  $HF/Comfy-Org/Pixal3D/resolve/main/diffusion_models/pixal3d_int8_convrot.safetensors &
wait
# Vidéo
get vae              $HF/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_int8_convrot.safetensors &
get vae              $HF/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors &
get diffusion_models $HF/Comfy-Org/MiniMax-H3/resolve/main/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors &
get text_encoders    $HF/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors &
get loras            $HF/lightx2v/Minimax-h3-Turbo/resolve/main/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors &
wait
echo TERMINÉ
