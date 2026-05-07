#!/usr/bin/env python3
"""Generate poster video with transitions, background music, and map dot animation overlay."""

import argparse
import glob
import os
import requests
import sqlite3
import subprocess
import warnings

warnings.filterwarnings("ignore")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    POSTER_DIR = os.path.join(PROJECT_ROOT, "city_posters")
    LOCAL_CJK_FONT_FILE_CANDIDATES = (
        os.path.join(PROJECT_ROOT, "fonts", "AlibabaPuHuiTi-Regular.ttf"),
        os.path.join(PROJECT_ROOT, "fonts", "AlibabaPuHuiTi-Bold.ttf"),
        os.path.join(PROJECT_ROOT, "fonts", "ZenMaruGothic-Regular.ttf"),
        os.path.join(PROJECT_ROOT, "fonts", "ZenMaruGothic-Bold.ttf"),
    )
    os.chdir(POSTER_DIR)

    # ── Poster images ──────────────────────────────────────────────────────────

    city_order = []
    cities_path = os.path.join(PROJECT_ROOT, "data", "cities_used.txt")
    with open(cities_path, encoding="utf-8") as _f:
        for line in _f:
            line = line.strip()
            if line and not line[0].isdigit():
                city_order.append(line)
            elif line:
                parts = line.split("\t")
                if len(parts) >= 2:
                    city_order.append(parts[-1].strip())

    files = []
    for city in city_order:
        candidate = f"{city}.png"
        if os.path.exists(candidate):
            files.append(candidate)

    existing_set = set(glob.glob("*.png")) - {f for f in files}
    existing = sorted(f for f in existing_set if "cities_map" not in f.lower())
    files.extend(existing)

    if not files:
        print("No poster images found!")
        return 1

    N = len(files)
    city_names = [os.path.splitext(f)[0] for f in files]
    print(f"Found {N} poster images")
    for i, fn in enumerate(files):
        print(f"  {i+1:2d}. {fn}")

    # ── Video parameters ───────────────────────────────────────────────────────

    xfade_types = [
        "fade",
        "dissolve",
        "fadeblack",
        "fadewhite",
        "hblur",
        "fadegrays",
        "zoomin",
    ]

    transition_overrides = {
        # `dissolve` here looks like corruption because the pair is very light -> very dark.
        ("三沙", "晋中"): "fade",
        ("晋中", "常州"): "fade",
        # Tail transitions are visually unstable with bright flash / directional blur.
        ("石嘴山", "伊春"): "fade",
        ("伊春", "桂林"): "fade",
    }

    D = 3.0  # seconds per image
    T = 0.5  # transition overlap
    FPS = 30
    CRF = 18
    INPUT_DURATION = 5.0
    total_duration = N * D - (N - 1) * T

    # ── Map animation figure dimensions (needed globally for overlay calc) ────

    MAP_FIG_W, MAP_FIG_H = 5.5, 4.0  # inches

    # ── Download background music ─────────────────────────────────────────────

    MUSIC_URL = (
        "https://cdn.pixabay.com/download/audio/2025/02/19/audio_8305fa59d7.mp3"
        "?filename=evgeniach-ambient-corporate-uplifting-302923.mp3"
    )
    MUSIC_FILE = "/tmp/poster_bg_music.mp3"
    WAV_FILE = "/tmp/poster_bg_music.wav"

    if not os.path.exists(WAV_FILE):
        print("Downloading background music...")
        r = requests.get(MUSIC_URL, timeout=30)
        with open(MUSIC_FILE, "wb") as bf:
            bf.write(r.content)
        print(f"  Downloaded {len(r.content) // 1024} KB")

        print("  Converting to WAV...")
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                MUSIC_FILE,
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                WAV_FILE,
            ],
            capture_output=True,
            check=True,
        )
    else:
        print("Background music already cached.")

    # ── Step 1: Generate poster video (temp) ──────────────────────────────────

    POSTER_TEMP = "/tmp/poster_temp.mp4"

    cmd = ["ffmpeg", "-y"]

    for f in files:
        cmd.extend(["-loop", "1", "-t", str(INPUT_DURATION), "-i", f])

    cmd.extend(["-i", WAV_FILE])

    parts = []

    for i in range(N):
        parts.append(
            f"[{i}:v]settb=AVTB,scale=1240:1754:flags=lanczos,setsar=1,"
            f"format=yuv420p,fps={FPS}[v{i}];"
        )

    for j in range(N - 1):
        transition_pair = (city_names[j], city_names[j + 1])
        trans = transition_overrides.get(
            transition_pair, xfade_types[j % len(xfade_types)]
        )
        offset = (j + 1) * (D - T)
        first = f"v{j}" if j == 0 else f"c{j-1}"
        second = f"v{j+1}"
        parts.append(
            f"[{first}][{second}]xfade=transition={trans}:duration={T}:offset={offset}[c{j}];"
        )

    fade_out_start = total_duration - 0.5
    last_in = f"c{N-2}" if N > 1 else "v0"
    parts.append(
        f"[{last_in}]trim=duration={total_duration},setpts=PTS-STARTPTS,"
        f"fade=t=in:st=0:d=0.5,fade=t=out:st={fade_out_start}:d=0.5,"
        f"format=yuv420p[vout];"
    )

    parts.append(
        f"[{N}:a]atrim=duration={total_duration},"
        f"afade=t=in:d=2,afade=t=out:st={total_duration-2}:d=2,"
        f"volume=0.5[about]"
    )

    filter_complex = "".join(parts)
    cmd.extend(["-filter_complex", filter_complex])
    cmd.extend(["-map", "[vout]"])
    cmd.extend(["-map", "[about]"])
    cmd.extend(["-c:v", "libx264", "-preset", "medium", "-crf", str(CRF)])
    cmd.extend(["-pix_fmt", "yuv420p"])
    cmd.extend(["-c:a", "aac", "-b:a", "128k"])
    cmd.extend([POSTER_TEMP])

    t_names = [
        transition_overrides.get(
            (city_names[i], city_names[i + 1]),
            xfade_types[i % len(xfade_types)],
        )
        for i in range(N - 1)
    ]
    print(f"\nDuration per image: {D}s, transition: {T}s")
    print(f"Total duration: {total_duration:.1f}s")
    print(f"Transitions ({len(t_names)}): {', '.join(t_names)}")
    print("Generating poster video...\n")

    subprocess.run(cmd, check=True)
    print("  Poster video done.\n")

    # ── Step 2: Generate map animation with stamp dots ────────────────────────

    MAP_ANIM = "/tmp/map_anim.mp4"

    def _get_city_coords():
        db_path = os.path.join(PROJECT_ROOT, "data", "city_geocode_cache.db")
        if not os.path.exists(db_path):
            return []
        coords = []
        with sqlite3.connect(db_path) as conn:
            for name in city_names:
                row = conn.execute(
                    "SELECT lat, lon FROM geocache WHERE city = ?", (name,)
                ).fetchone()
                if row:
                    coords.append((name, row[0], row[1]))
        return coords

    def _load_world_geodata():
        try:
            import geopandas as gpd
        except ImportError:
            return None
        gpkg = os.path.join(PROJECT_ROOT, "data", "ne_110m_countries.gpkg")
        if os.path.exists(gpkg):
            try:
                return gpd.read_file(gpkg)
            except Exception:
                pass
        return None

    def _setup_matplotlib_font():
        try:
            import matplotlib
            from matplotlib.font_manager import FontProperties

            for path in LOCAL_CJK_FONT_FILE_CANDIDATES:
                if os.path.exists(path):
                    fp = FontProperties(fname=path)
                    matplotlib.font_manager.fontManager.addfont(path)
                    matplotlib.rcParams["font.family"] = fp.get_name()
                    matplotlib.rcParams["axes.unicode_minus"] = False
                    return

            result = subprocess.run(
                ["fc-list", ":lang=zh", "-f", "%{file}\n"],
                capture_output=True,
                text=True,
                check=True,
            )
            for line in result.stdout.splitlines():
                path = line.strip()
                if path and os.path.exists(path):
                    fp = FontProperties(fname=path)
                    matplotlib.font_manager.fontManager.addfont(path)
                    matplotlib.rcParams["font.family"] = fp.get_name()
                    matplotlib.rcParams["axes.unicode_minus"] = False
                    return
        except Exception:
            pass

    def _generate_map_animation(city_coords):
        import numpy as np
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from PIL import Image, ImageDraw

        if len(city_coords) < 2:
            return None

        # Calculate view bounds
        all_lons = [c[2] for c in city_coords]
        all_lats = [c[1] for c in city_coords]
        min_lon, max_lon = min(all_lons), max(all_lons)
        min_lat, max_lat = min(all_lats), max(all_lats)
        pad_lon = max((max_lon - min_lon) * 0.15, 4)
        pad_lat = max((max_lat - min_lat) * 0.15, 3)
        view_min_lon = max(70, min_lon - pad_lon)
        view_max_lon = min(140, max_lon + pad_lon)
        view_min_lat = max(15, min_lat - pad_lat)
        view_max_lat = min(55, max_lat + pad_lat)

        # Load world map
        world = _load_world_geodata()
        if world is not None:
            try:
                world_c = world.cx[view_min_lon:view_max_lon, view_min_lat:view_max_lat]
            except Exception:
                world_c = world
            if getattr(world_c, "empty", False):
                world_c = world
        else:
            world_c = None

        _setup_matplotlib_font()

        # Render base map once (country outlines, no dots)
        DPI = 120
        fig = Figure(figsize=(MAP_FIG_W, MAP_FIG_H), dpi=DPI)
        fig.set_facecolor("#DDECF8")
        fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
        ax = fig.subplots(1, 1)
        ax.set_facecolor("#DDECF8")

        if world_c is not None:
            world_c.plot(
                ax=ax,
                color="#ECE9E1",
                edgecolor="#B0B5BB",
                linewidth=0.6,
                zorder=1,
            )

        ax.set_xlim(view_min_lon, view_max_lon)
        ax.set_ylim(view_min_lat, view_max_lat)
        ax.set_aspect("equal")
        ax.axis("off")

        # Draw to populate transforms
        canvas = FigureCanvasAgg(fig)
        canvas.draw()

        # Get pixel positions for all cities (flip y for PIL top-left origin)
        fig_w_px = int(MAP_FIG_W * DPI)
        fig_h_px = int(MAP_FIG_H * DPI)
        x0, y0 = ax.transData.transform((view_min_lon, view_min_lat))
        x1, y1 = ax.transData.transform((view_max_lon, view_max_lat))
        pad_px = 2
        crop_left = max(0, int(round(min(x0, x1))) - pad_px)
        crop_right = min(fig_w_px, int(round(max(x0, x1))) + pad_px)
        crop_top = max(0, fig_h_px - int(round(max(y0, y1))) - pad_px)
        crop_bottom = min(fig_h_px, fig_h_px - int(round(min(y0, y1))) + pad_px)

        if crop_right <= crop_left or crop_bottom <= crop_top:
            crop_left, crop_top, crop_right, crop_bottom = 0, 0, fig_w_px, fig_h_px

        dot_px = []
        for _, lat, lon in city_coords:
            dx, dy = ax.transData.transform((lon, lat))
            dot_px.append(
                (
                    int(round(dx)) - crop_left,
                    fig_h_px - int(round(dy)) - crop_top,
                )
            )

        # Save base map image
        BASE_MAP = "/tmp/map_base.png"
        fig.savefig(BASE_MAP, dpi=DPI, facecolor=fig.get_facecolor())
        plt.close(fig)

        # ── Generate animation frames, pipe to ffmpeg ──

        MAP_FPS = 10
        total_map_frames = round(total_duration * MAP_FPS)
        FRAMES_PER_CITY = total_map_frames // len(city_coords)
        extra = total_map_frames % len(city_coords)
        STAMP_FRAMES = min(int(0.5 * MAP_FPS), FRAMES_PER_CITY)
        base_img = (
            Image.open(BASE_MAP)
            .convert("RGBA")
            .crop((crop_left, crop_top, crop_right, crop_bottom))
        )
        MAP_W = 400
        MAP_H = max(1, round(MAP_W * base_img.height / base_img.width))

        # Stamp scale curve: 0 → overshoot → settle
        stamp_scales = []
        for f in range(STAMP_FRAMES):
            t = f / STAMP_FRAMES
            scale = 1.0 - (1.0 - t) ** 3
            scale += 0.25 * np.sin(t * np.pi) * (1.0 - t)
            stamp_scales.append(max(0.0, scale))

        total_frames = len(city_coords) * FRAMES_PER_CITY + extra

        ffmpeg_proc = subprocess.Popen(
            [
                "ffmpeg",
                "-y",
                "-f",
                "rawvideo",
                "-vcodec",
                "rawvideo",
                "-s",
                f"{MAP_W}x{MAP_H}",
                "-pix_fmt",
                "rgb24",
                "-r",
                str(MAP_FPS),
                "-i",
                "-",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "fast",
                "-crf",
                "23",
                MAP_ANIM,
            ],
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        frame_idx = 0
        for ci in range(len(city_coords)):
            n_frames = FRAMES_PER_CITY + (1 if ci < extra else 0)
            for f in range(n_frames):
                frame = base_img.copy()
                draw = ImageDraw.Draw(frame)

                # Previous dots (static, full size)
                for j in range(ci):
                    px, py = dot_px[j]
                    r = 5
                    draw.ellipse(
                        [px - r, py - r, px + r, py + r],
                        fill="#E76F51",
                        outline="white",
                        width=2,
                    )

                # Current dot (stamp animation)
                scale = stamp_scales[f] if f < STAMP_FRAMES else 1.0
                px, py = dot_px[ci]
                r = max(1, int(5 * scale))
                ow = max(1, int(2 * scale))
                draw.ellipse(
                    [px - r, py - r, px + r, py + r],
                    fill="#F4A261",
                    outline="#264653",
                    width=ow,
                )

                frame_resized = frame.resize((MAP_W, MAP_H), Image.Resampling.LANCZOS)
                ffmpeg_proc.stdin.write(frame_resized.convert("RGB").tobytes())
                frame_idx += 1

            if (ci + 1) % 5 == 0 or ci == len(city_coords) - 1:
                print(f"  Map frames: {frame_idx}/{total_frames}")

        ffmpeg_proc.stdin.close()
        ffmpeg_proc.wait()

        if ffmpeg_proc.returncode != 0:
            return None

        size_mb = os.path.getsize(MAP_ANIM) / (1024 * 1024)
        print(f"  Map animation video: {size_mb:.1f} MB")
        return MAP_H / MAP_W

    # ── Generate map animation ──

    print("Generating map animation...")
    city_coords = _get_city_coords()
    map_ok = False
    if city_coords:
        try:
            generated_map_aspect = _generate_map_animation(city_coords)
            if generated_map_aspect is not None:
                map_ok = True
        except Exception as e:
            print(f"  Map animation failed: {e}")
            import traceback

            traceback.print_exc()
    else:
        print("  No city coordinates found, skipping.")

    # ── Step 3: Overlay map on poster (or just copy) ──────────────────────────

    FINAL_OUTPUT = os.path.join(PROJECT_ROOT, "posters_video_music.mp4")

    if map_ok and os.path.exists(MAP_ANIM):
        print("\nOverlaying map animation on poster video...")
        overlay_w = 200
        overlay_h = 200
        pos_x = 1240 - overlay_w - 25
        pos_y = 25

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                POSTER_TEMP,
                "-i",
                MAP_ANIM,
                "-filter_complex",
                f"[1:v]scale={overlay_w}:{overlay_h}[map];"
                f"[0:v][map]overlay={pos_x}:{pos_y}:enable='between(t,0,{total_duration})',"
                f"setpts=PTS-STARTPTS[vout]",
                "-map",
                "[vout]",
                "-map",
                "0:a",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                str(CRF),
                "-c:a",
                "copy",
                FINAL_OUTPUT,
            ],
            check=True,
        )
    else:
        print("\nNo map animation, copying poster video directly...")
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                POSTER_TEMP,
                "-c",
                "copy",
                FINAL_OUTPUT,
            ],
            check=True,
        )

    size_mb = os.path.getsize(FINAL_OUTPUT) / (1024 * 1024)
    print(f"\nDone! Output: {FINAL_OUTPUT} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    raise SystemExit(main())
