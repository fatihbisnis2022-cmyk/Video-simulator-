import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class SimulationScenario:
    name: str
    description: str
    parameters: dict


def _escape_drawtext(teks: str) -> str:
    """Amankan karakter khusus agar tidak merusak sintaks drawtext ffmpeg."""
    return (
        teks.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def _bangun_vf_filter(scenario_name: str, params: dict) -> str:
    """Susun rantai filter ffmpeg termasuk variasi arah kamera (pan/tilt/zoom/crop)."""
    lebar = int(params.get("lebar", 640))
    tinggi = int(params.get("tinggi", 360))
    fps = int(params.get("fps", 24))
    durasi = float(params.get("durasi_detik", 5.0))
    arah = str(params.get("arah_kamera", "tengah")).lower()
    visibilitas = float(params.get("visibilitas", 1.0))
    intensitas = float(params.get("intensitas_efek", 0.3))
    waktu = str(params.get("waktu", "siang")).lower()
    tint = str(params.get("tint", "netral")).lower()
    tampilkan_hud = bool(params.get("tampilkan_hud", False))

    # Pastikan dimensi genap untuk codec libx264 + yuv420p
    lebar = max(160, lebar - (lebar % 2))
    tinggi = max(120, tinggi - (tinggi % 2))
    durasi = max(1.0, durasi)
    visibilitas = max(0.1, min(1.2, visibilitas))
    intensitas = max(0.0, min(1.0, intensitas))

    # Rasio crop: lebih kecil = lebih zoom, lebih besar = ruang gerak pan/tilt
    rasio_crop = 0.72 if "zoom" in arah else 0.80
    cw = f"min(iw\\,max(2\\,2*floor((iw*{rasio_crop})/2)))"
    ch = f"min(ih\\,max(2\\,2*floor((ih*{rasio_crop})/2)))"
    avail_x = f"max(0\\,iw-{cw})"
    avail_y = f"max(0\\,ih-{ch})"
    prog = f"min(1\\,max(0\\,t/{durasi:.2f}))"

    if arah == "pan_kiri_kanan":
        cx, cy = f"({avail_x})*{prog}", f"({avail_y})*0.5"
    elif arah == "pan_kanan_kiri":
        cx, cy = f"({avail_x})*(1-{prog})", f"({avail_y})*0.5"
    elif arah == "tilt_atas_bawah":
        cx, cy = f"({avail_x})*0.5", f"({avail_y})*{prog}"
    elif arah == "tilt_bawah_atas":
        cx, cy = f"({avail_x})*0.5", f"({avail_y})*(1-{prog})"
    elif arah == "kiri_atas":
        cx, cy = "0", "0"
    elif arah == "kanan_bawah":
        cx, cy = avail_x, avail_y
    elif arah in {"zoom_tengah", "tengah"}:
        cx, cy = f"({avail_x})*0.5", f"({avail_y})*0.5"
    else:
        cx, cy = f"({avail_x})*0.5", f"({avail_y})*0.5"

    filters: list[str] = [
        f"fps={fps}",
        f"crop=w={cw}:h={ch}:x='{cx}':y='{cy}':exact=1",
        f"scale={lebar}:{tinggi}:flags=lanczos",
        "format=yuv420p",
    ]

    # 1. Pencahayaan & kontras dasar menurut waktu dan visibilitas
    if waktu == "malam":
        brightness = -0.22 * (0.5 + 0.5 * intensitas)
        saturation = 0.65
    elif waktu == "senja":
        brightness = -0.06
        saturation = 1.15
    elif waktu == "pagi":
        brightness = 0.04
        saturation = 0.85
    else:  # siang
        brightness = 0.03
        saturation = 1.05

    contrast = max(0.55, min(1.45, 0.72 + 0.38 * visibilitas))
    filters.append(
        f"eq=brightness={brightness:.3f}:contrast={contrast:.3f}:saturation={saturation:.3f}"
    )

    # 2. Nuansa warna (colorbalance)
    if tint == "dingin":
        filters.append("colorbalance=rs=-0.10:gs=-0.03:bs=0.14")
    elif tint == "hangat":
        filters.append("colorbalance=rs=0.14:gs=0.04:bs=-0.12")

    # 3. Efek kabut / penurunan ketajaman saat visibilitas rendah
    if visibilitas < 0.75:
        blur_sigma = round((0.75 - visibilitas) * 6.0 * (0.5 + intensitas), 2)
        if blur_sigma > 0:
            filters.append(f"gblur=sigma={blur_sigma}")
        if intensitas > 0.3:
            kabut_alpha = round(min(0.35, (0.75 - visibilitas) * 0.45 * intensitas), 3)
            filters.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=white@{kabut_alpha}:t=fill")
    else:
        sharpen_amount = round(0.4 + 0.6 * intensitas, 2)
        filters.append(f"unsharp=5:5:{sharpen_amount}:5:5:0.0")

    # 4. Tekstur/noise dan vignette
    if intensitas > 0.15:
        noise_strength = int(min(25, round(intensitas * 18)))
        filters.append(f"noise=alls={noise_strength}:allf=t+u")

    if waktu in {"malam", "senja"} or intensitas >= 0.5:
        filters.append("vignette=PI/5")

    # 5. Opsional: label informasi skenario di pojok kiri atas
    if tampilkan_hud:
        label_aman = _escape_drawtext(
            f"{scenario_name} | arah={arah} | vis={visibilitas:.2f}"
        )
        filters.append(
            f"drawbox=x=10:y=10:w=400:h=34:color=black@0.55:t=fill,"
            f"drawtext=text='{label_aman}':x=18:y=19:fontsize=15:fontcolor=white"
        )

    return ",".join(filters)


def jalankan_batch_simulasi_stream(
    sumber_video: Path,
    skenario_list: list[SimulationScenario],
    output_root: Path = Path("./simulasi_out"),
) -> Iterator[dict]:
    if not sumber_video.exists():
        raise FileNotFoundError(f"Video sumber tidak ditemukan: {sumber_video}")

    output_root.mkdir(parents=True, exist_ok=True)
    total = len(skenario_list)

    if total == 0:
        yield {
            "event": "done",
            "progress": 1.0,
            "message": "Tidak ada skenario untuk dijalankan.",
            "results": [],
        }
        return

    results: list[dict] = []

    for idx, skenario in enumerate(skenario_list, start=1):
        folder_skenario = output_root / skenario.name
        folder_skenario.mkdir(parents=True, exist_ok=True)
        out_video = folder_skenario / f"{skenario.name}.mp4"

        yield {
            "event": "scenario_start",
            "index": idx,
            "total": total,
            "scenario": skenario.name,
            "progress": (idx - 1) / total,
            "message": f"[{idx}/{total}] Memproses skenario '{skenario.name}'...",
            "video_path": None,
        }

        vf_filter = _bangun_vf_filter(skenario.name, skenario.parameters)

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(sumber_video),
            "-vf",
            vf_filter,
            "-c:v",
            "libx264",
            "-crf",
            "23",
            "-preset",
            "fast",
            "-an",
            str(out_video),
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            item = {
                "name": skenario.name,
                "description": skenario.description,
                "parameters": skenario.parameters,
                "video_path": str(out_video),
                "status": "selesai",
            }
            (folder_skenario / "manifest.json").write_text(
                json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            results.append(item)

            yield {
                "event": "scenario_done",
                "index": idx,
                "total": total,
                "scenario": skenario.name,
                "progress": idx / total,
                "message": f"[{idx}/{total}] Selesai: '{skenario.name}'",
                "video_path": str(out_video),
            }
        except FileNotFoundError:
            pesan_err = (
                "Binary 'ffmpeg' tidak ditemukan. Pastikan 'ffmpeg' ada di packages.txt "
                "lalu reboot aplikasi."
            )
            item = {
                "name": skenario.name,
                "description": skenario.description,
                "parameters": skenario.parameters,
                "video_path": None,
                "status": f"gagal: {pesan_err}",
            }
            results.append(item)
            yield {
                "event": "scenario_error",
                "index": idx,
                "total": total,
                "scenario": skenario.name,
                "progress": idx / total,
                "message": f"[{idx}/{total}] Gagal pada '{skenario.name}': {pesan_err}",
                "video_path": None,
            }
        except subprocess.CalledProcessError as exc:
            err_tail = (exc.stderr or "")[-500:]
            item = {
                "name": skenario.name,
                "description": skenario.description,
                "parameters": skenario.parameters,
                "video_path": None,
                "status": f"gagal: {err_tail}",
            }
            results.append(item)
            yield {
                "event": "scenario_error",
                "index": idx,
                "total": total,
                "scenario": skenario.name,
                "progress": idx / total,
                "message": f"[{idx}/{total}] Gagal pada '{skenario.name}': {err_tail}",
                "video_path": None,
            }

    yield {
        "event": "done",
        "progress": 1.0,
        "message": f"Batch selesai ({len([r for r in results if r['status'] == 'selesai'])}/{total} berhasil).",
        "results": results,
    }
