# Rebuilding the demo video

Two steps. `make_cards.py` renders the title card, the end card and the two
speed badges as PNGs -- in Chrome, not with ffmpeg's drawtext, so the typography
is real and the badges have genuine transparency to overlay on the footage.

    .venv\Scripts\python.exe Use_Cases\video\make_cards.py <output-dir>

Then ffmpeg assembles it. There is no system ffmpeg; the one used came from
`pip install imageio-ffmpeg`, which ships a static binary inside the venv:

    .venv\Scripts\python.exe -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"

The cut is: 5s title, the first 40s at 2x, the remainder at 6x, 5s end card.
`split` is used so the 316 MB source is decoded once rather than twice.

    ffmpeg -y -loop 1 -t 5 -i title.png -i raw.mov -i badge2x.png -i badge6x.png \
      -loop 1 -t 5 -i end.png -filter_complex "\
    [1:v]split=2[v1][v2];\
    [v1]trim=0:40,setpts=(PTS-STARTPTS)/2,fps=30[a0];[a0][2:v]overlay=0:0[A];\
    [v2]trim=start=40,setpts=(PTS-STARTPTS)/6,fps=30[b0];[b0][3:v]overlay=0:0[B];\
    [0:v]fps=30,setpts=PTS-STARTPTS[T];[4:v]fps=30,setpts=PTS-STARTPTS[E];\
    [T][A][B][E]concat=n=4:v=1:a=0,format=yuv420p[out]" -map "[out]" \
      -c:v libx264 -preset slow -crf 26 -pix_fmt yuv420p \
      -profile:v high -level 5.2 -movflags +faststart out.mp4

Resolution is kept at the source 2880x1800 deliberately -- the size saving comes
from CRF and from the speed-up, not from downscaling. 316 MB in, 10.5 MB out.

The numbers on the end card come from `logs/<run>/summary.json`. Update them
there if the video is rebuilt from a different run.
