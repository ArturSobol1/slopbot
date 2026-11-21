"""Test: animate one photo into a ~5 s clip with Wan 2.2 TI2V-5B on the local GPU.

Usage (from the SlopBot folder):
  .venv-video\\Scripts\\python tools\\test_i2v.py <photo> <out.mp4> "<prompt>" [steps]

Fitting a 12 GB card: the 5B video model is stored in 8-bit (fp8) and computed in bf16, and the
big text encoder runs once on the CPU and is then dropped, so the rest fits on the GPU.
"""
import sys
import time
from pathlib import Path

import torch
from PIL import Image, ImageOps
from diffusers import AutoencoderKLWan, WanImageToVideoPipeline
from diffusers.utils import export_to_video

MODEL = Path(__file__).resolve().parent.parent / "models" / "Wan2.2-TI2V-5B-Diffusers"
W, H, FRAMES, FPS = 1280, 704, 121, 24  # the model's native 720p, 121 frames = ~5 s
NEGATIVE = ("blurry, low quality, distorted, deformed, morphing, warped shapes, extra wings, "
            "duplicate objects, static, frozen, jpeg artifacts, watermark, text, subtitles")


def main() -> None:
    src, out, prompt = sys.argv[1], sys.argv[2], sys.argv[3]
    steps = int(sys.argv[4]) if len(sys.argv) > 4 else 40

    t0 = time.time()
    vae = AutoencoderKLWan.from_pretrained(MODEL, subfolder="vae", torch_dtype=torch.float32)
    pipe = WanImageToVideoPipeline.from_pretrained(MODEL, vae=vae, torch_dtype=torch.bfloat16)

    # Encode the prompt once on the CPU, then drop the ~11 GB text encoder.
    with torch.no_grad():
        prompt_embeds, negative_embeds = pipe.encode_prompt(
            prompt=prompt, negative_prompt=NEGATIVE, do_classifier_free_guidance=True,
            num_videos_per_prompt=1, max_sequence_length=512, device=torch.device("cpu"))
    pipe.text_encoder = None
    pipe.transformer.enable_layerwise_casting(storage_dtype=torch.float8_e4m3fn,
                                             compute_dtype=torch.bfloat16)
    pipe.to("cuda")  # with 8-bit storage the video model (~5 GB) and VAE fit on the card together
    try:
        pipe.vae.enable_tiling()
    except Exception:
        pass
    print(f"loaded in {time.time() - t0:.0f}s", flush=True)

    image = ImageOps.fit(Image.open(src).convert("RGB"), (W, H))
    torch.cuda.reset_peak_memory_stats()
    t1 = time.time()
    frames = pipe(image=image, prompt_embeds=prompt_embeds.to("cuda", torch.bfloat16),
                  negative_prompt_embeds=negative_embeds.to("cuda", torch.bfloat16),
                  height=H, width=W, num_frames=FRAMES, guidance_scale=5.0,
                  num_inference_steps=steps).frames[0]
    took = time.time() - t1
    export_to_video(frames, out, fps=FPS)
    print(f"generated {len(frames)} frames ({len(frames) / FPS:.1f}s of video) in {took / 60:.1f} min "
          f"with {steps} steps; peak GPU memory {torch.cuda.max_memory_allocated() / 1e9:.1f} GB", flush=True)


if __name__ == "__main__":
    main()
