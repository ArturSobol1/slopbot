"""Assembles the final 1080x1920 video with ffmpeg."""
import random
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from . import ff
from .config import FPS, H, W

# Every segment ends in exactly the same pixel format and color range. Mixing formats (photos come
# out full-range "yuvj420p") makes ffmpeg rebuild its filters mid-video at every switch.
UNIFORM = "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
FILL = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},{UNIFORM}"
FG_Y = 820  # vertical center of a widescreen picture (captions sit below it)
# Widescreen clip: full width in the upper middle, over a blurred, darkened copy of itself.
LETTERBOX = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
             f"gblur=sigma=30,eq=brightness=-0.22[bg];[b]scale={W}:-2[fg];"
             f"[bg][fg]overlay=0:{FG_Y}-h/2,setsar=1,fps={FPS},{UNIFORM}")
ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
          "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
LOUDNESS = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100"


MUSIC_LEVEL = "loudnorm=I=-24:TP=-6:LRA=7"  # music bed before ducking: ends up ~13 dB under the voice


def _mix_audio(run_dir: Path, music: Path | None, total: float, music_start: float = 0.0) -> str:
    """Levels the voice (and mixes in music) as its own step, so video processing can never
    disturb the audio. The music ducks under the voice, fades in and fades out at the end.
    Returns the mixed file's name inside run_dir."""
    inputs = ["-i", "voice.wav"]
    if music:
        inputs += ["-stream_loop", "-1", "-ss", f"{music_start:.2f}", "-i", str(music)]
        fade_out = max(total - 1.8, 0)
        graph = (f"[0:a]aresample=44100,apad=whole_dur={total:.3f},asplit=2[voice][key];"
                 f"[1:a]aresample=44100,{MUSIC_LEVEL},atrim=0:{total:.3f},afade=t=in:d=0.6,"
                 f"afade=t=out:st={fade_out:.3f}:d=1.8[bed];"
                 f"[bed][key]sidechaincompress=threshold=0.015:ratio=4:attack=20:release=400[m];"
                 f"[voice][m]amix=inputs=2:duration=first:normalize=0,{LOUDNESS}[a]")
    else:
        graph = f"[0:a]{LOUDNESS}[a]"
    ff.run([*inputs, "-filter_complex", graph, "-map", "[a]", "-c:a", "pcm_s16le", "mix.wav"], cwd=run_dir)
    return "mix.wav"


def _placeholder_input(seed: int) -> list:
    return ["-f", "lavfi", "-i", f"gradients=s={W}x{H}:r={FPS}:speed=0.015:seed={seed}"]


def _photo_frame(src: Path, out: Path) -> None:
    """Builds a 2x-resolution still: the photo over a blurred copy of itself (zoomed later)."""
    img = Image.open(src).convert("RGB")
    w2, h2 = W * 2, H * 2
    frame = ImageOps.fit(img, (w2, h2)).filter(ImageFilter.GaussianBlur(60))
    frame = ImageEnhance.Brightness(frame).enhance(0.55)
    if img.height > img.width * 1.2:  # tall photos simply fill the screen
        frame = ImageOps.fit(img, (w2, h2))
    else:
        fg_w = w2
        fg_h = round(img.height * fg_w / img.width)
        if fg_h > 2600:
            fg_h = 2600
            fg_w = round(img.width * fg_h / img.height)
        fg = img.resize((fg_w, fg_h), Image.LANCZOS)
        frame.paste(fg, ((w2 - fg_w) // 2, FG_Y * 2 - fg_h // 2))
    frame.save(out, quality=92)


def _segment(shot, frames: int, out: Path, index: int) -> None:
    if shot is None:
        ff.run([*_placeholder_input(index * 7 + 1), "-frames:v", frames, "-vf", FILL, "-an", *ENCODE, out])
    elif shot.kind == "photo":
        still = out.with_suffix(".jpg")
        _photo_frame(shot.path, still)
        zoom = f"1+0.08*on/{frames}" if index % 2 == 0 else f"1.08-0.08*on/{frames}"  # alternate in/out
        vf = (f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS},"
              f"setsar=1,{UNIFORM}")
        ff.run(["-loop", "1", "-framerate", FPS, "-i", still, "-frames:v", frames, "-vf", vf, "-an", *ENCODE, out])
    else:
        vf = FILL if shot.portrait else LETTERBOX
        seek = ["-ss", f"{shot.start:.2f}"] if shot.start else []
        ff.run([*seek, "-stream_loop", "-1", "-i", shot.path, "-frames:v", frames, "-vf", vf, "-an", *ENCODE,
                "-r", FPS, out])


def explainer(run_dir: Path, shots: list, starts: list[float], total: float, music: Path | None,
              music_start: float = 0.0) -> Path:
    """One picture per line, cut on the line boundaries, then captions + audio on top."""
    seg_dir = run_dir / "segments"
    seg_dir.mkdir(exist_ok=True)
    # Snap cut points to whole frames so the picture never drifts from the voice.
    bounds = [round(s * FPS) for s in starts] + [round(total * FPS)]
    bounds[0] = 0
    listing = []
    for i, shot in enumerate(shots):
        out = seg_dir / f"seg_{i:03}.mp4"
        _segment(shot, max(bounds[i + 1] - bounds[i], 1), out, i)
        listing.append(f"file '{out.name}'")
    (seg_dir / "list.txt").write_text("\n".join(listing), encoding="utf-8")
    ff.run(["-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", run_dir / "video_only.mp4"],
           cwd=seg_dir)

    audio = _mix_audio(run_dir, music, total, music_start)
    final = run_dir / "final.mp4"
    ff.run(["-i", "video_only.mp4", "-i", audio, "-vf", f"ass=captions.ass,{UNIFORM}",
            "-map", "0:v", "-map", "1:a", *ENCODE, "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}",
            "-movflags", "+faststart", final.name], cwd=run_dir)
    return final


def dialogue(run_dir: Path, background: Path | None, characters: dict,
             speaking: dict[str, list[tuple[float, float]]], total: float, music: Path | None,
             music_start: float = 0.0) -> Path:
    """Gameplay background, the current speaker's picture at the bottom, captions in the middle."""
    if background:
        length = ff.duration(background)
        offset = random.uniform(0, max(length - total - 1, 0))
        inputs = ["-ss", f"{offset:.2f}", "-stream_loop", "-1", "-i", background]
    else:
        inputs = _placeholder_input(random.randint(1, 1000))
    audio = _mix_audio(run_dir, music, total, music_start)
    inputs += ["-i", audio,
               "-loop", "1", "-i", characters["A"]["image_path"],
               "-loop", "1", "-i", characters["B"]["image_path"]]

    def enable(spans):
        return "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in spans) or "0"

    bob = "8*abs(sin(t*9))"  # little bounce while talking
    graph = (
        f"[0:v]{FILL}[bg];"
        f"[2:v]scale=-1:760[ca];[3:v]scale=-1:760[cb];"
        f"[bg][ca]overlay=x=10:y='H-h-40-{bob}':enable='{enable(speaking['A'])}'[t1];"
        f"[t1][cb]overlay=x='W-w-10':y='H-h-40-{bob}':enable='{enable(speaking['B'])}'[t2];"
        f"[t2]ass=captions.ass,{UNIFORM}[v]"
    )
    final = run_dir / "final.mp4"
    ff.run([*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "1:a",
            *ENCODE, "-r", FPS, "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}",
            "-movflags", "+faststart", final.name], cwd=run_dir)
    return final
