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


def _bangun_vf_filter(params: dict) -> str:
    """Pemetaan parameter simulasi ke filter ffmpeg (ganti dengan mesin simulasi asli)."""
    visibilitas = float(params.get("visibilitas", 1.0))
    waktu = str(params.get("waktu", "siang"))

    brightness = -0.18 if waktu == "malam" else 0.04
    contrast = max(0.6, min(1.4, 0.75 + 0.35 * visibilitas))
    return f"scale=640:-2,eq=brightness={brightness}:contrast={contrast}"


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

        vf_filter = _bangun_vf_filter(skenario.parameters)

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
