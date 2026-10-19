"""Outils vidéo exécutés dans le conteneur ComfyUI (PyAV) :
  lastframe <video> <image.png>         extrait la dernière image
  concat <sortie.mp4> <clip1> <clip2>…  recolle les clips (image + son), sans doubler l'image de raccord
"""

import sys
from fractions import Fraction

import av


def lastframe(src, dst):
    last = None
    with av.open(src) as c:
        for f in c.decode(video=0):
            last = f
    last.to_image().save(dst)


def concat(dst, clips):
    with av.open(clips[0]) as c0:
        v0 = c0.streams.video[0]
        width, height = v0.codec_context.width, v0.codec_context.height
        fps = v0.average_rate or Fraction(24)
        a0 = c0.streams.audio[0] if c0.streams.audio else None
        rate = a0.codec_context.sample_rate if a0 else None

    out = av.open(dst, "w", options={"movflags": "+faststart"})
    vs = out.add_stream("libx264", rate=fps)
    vs.width, vs.height, vs.pix_fmt = width, height, "yuv420p"
    vs.options = {"crf": "18", "preset": "medium"}
    as_ = None
    if rate:
        as_ = out.add_stream("aac", rate=rate)
        as_.layout = "stereo"
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=rate)
        fifo = av.AudioFifo()

    n = 0
    apts = 0
    for i, clip in enumerate(clips):
        with av.open(clip) as c:
            for j, f in enumerate(c.decode(video=0)):
                if i > 0 and j == 0:
                    continue  # image de raccord = dernière image du clip précédent
                img = f.reformat(width=width, height=height, format="yuv420p")
                frame = av.VideoFrame.from_ndarray(img.to_ndarray(), format="yuv420p")
                frame.pts = n
                frame.time_base = 1 / fps
                n += 1
                for p in vs.encode(frame):
                    out.mux(p)
        if as_:
            with av.open(clip) as c:
                if c.streams.audio:
                    skip = int(rate / fps) if i > 0 else 0  # durée de l'image sautée
                    for f in c.decode(audio=0):
                        f.pts = None
                        for rf in resampler.resample(f):
                            fifo.write(rf)
                    if skip:
                        fifo.read(min(skip, fifo.samples))
                while fifo.samples >= 1024:
                    af = fifo.read(1024)
                    af.pts = apts
                    af.time_base = Fraction(1, rate)
                    apts += 1024
                    for p in as_.encode(af):
                        out.mux(p)
    for p in vs.encode():
        out.mux(p)
    if as_:
        if fifo.samples:
            af = fifo.read()
            af.pts = apts
            af.time_base = Fraction(1, rate)
            for p in as_.encode(af):
                out.mux(p)
        for p in as_.encode():
            out.mux(p)
    out.close()
    print(f"{n} images, {n / float(fps):.1f} s")


if __name__ == "__main__":
    if sys.argv[1] == "lastframe":
        lastframe(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "concat":
        concat(sys.argv[2], sys.argv[3:])
