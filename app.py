import json
import tempfile
from pathlib import Path

import streamlit as st

from video_sim_pipeline import (
    SimulationScenario,
    jalankan_batch_simulasi_stream,
)

st.set_page_config(page_title="Batch Simulasi Video", page_icon="🎬", layout="wide")
st.title("🎬 Pemantau Batch Simulasi Video")
st.caption("Unggah satu video asli, atur daftar skenario & arah kamera, lalu pantau progres tiap skenario.")

st.session_state.setdefault("sim_results", [])

default_scenarios_json = json.dumps(
    [
        {
            "name": "cam_pan_kiri_kanan",
            "description": "Kamera bergeser perlahan dari kiri ke kanan frame",
            "parameters": {
                "arah_kamera": "pan_kiri_kanan",
                "durasi_detik": 6,
                "lebar": 640,
                "tinggi": 360,
                "visibilitas": 1.0,
                "intensitas_efek": 0.2,
                "tampilkan_hud": True,
            },
        },
        {
            "name": "cam_tilt_atas_bawah",
            "description": "Kamera mengarah dari bagian atas ke bagian bawah adegan",
            "parameters": {
                "arah_kamera": "tilt_atas_bawah",
                "durasi_detik": 6,
                "lebar": 640,
                "tinggi": 360,
                "visibilitas": 0.9,
                "intensitas_efek": 0.25,
                "tampilkan_hud": True,
            },
        },
        {
            "name": "cam_zoom_tengah_malam",
            "description": "Fokus ke area tengah dengan nuansa malam dan visibilitas lebih rendah",
            "parameters": {
                "arah_kamera": "zoom_tengah",
                "waktu": "malam",
                "tint": "dingin",
                "lebar": 640,
                "tinggi": 360,
                "visibilitas": 0.5,
                "intensitas_efek": 0.65,
                "tampilkan_hud": True,
            },
        },
    ],
    indent=2,
    ensure_ascii=False,
)

uploaded_video = st.file_uploader("Pilih Video Asli (.mp4)", type=["mp4", "mov", "mkv"])
scenarios_raw = st.text_area("Daftar Skenario (JSON)", value=default_scenarios_json, height=280)

mulai_btn = st.button("🚀 Jalankan Batch Simulasi", type="primary", use_container_width=True)

progress_bar = st.progress(0.0, text="Menunggu dijalankan...")
status_box = st.status("Log Progres Skenario", expanded=True)
live_gallery = st.container()

if mulai_btn:
    if uploaded_video is None:
        st.error("Silakan unggah file video asli terlebih dahulu.")
        st.stop()

    try:
        parsed = json.loads(scenarios_raw)
        if not isinstance(parsed, list) or not parsed:
            raise ValueError("JSON harus berupa list berisi minimal 1 skenario.")
        scenarios = [
            SimulationScenario(
                name=str(item["name"]),
                description=str(item.get("description", "")),
                parameters=dict(item.get("parameters", {})),
            )
            for item in parsed
        ]
    except Exception as exc:
        st.error(f"Format JSON skenario tidak valid: {exc}")
        st.stop()

    tmp_dir = Path(tempfile.mkdtemp(prefix="vid_sim_"))
    input_path = tmp_dir / uploaded_video.name
    input_path.write_bytes(uploaded_video.getbuffer())

    st.session_state.sim_results = []
    final_results: list[dict] = []

    with live_gallery:
        cols = st.columns(2)
        col_idx = 0

        for ev in jalankan_batch_simulasi_stream(
            sumber_video=input_path,
            skenario_list=scenarios,
            output_root=tmp_dir / "output",
        ):
            pct = float(ev.get("progress", 0.0))
            msg = str(ev.get("message", ""))
            progress_bar.progress(min(max(pct, 0.0), 1.0), text=msg)
            status_box.write(msg)

            if ev.get("event") == "scenario_done" and ev.get("video_path"):
                with cols[col_idx % 2]:
                    st.markdown(f"**{ev['scenario']}**")
                    st.video(ev["video_path"])
                col_idx += 1
            elif ev.get("event") == "scenario_error":
                status_box.update(state="error")
            elif ev.get("event") == "done":
                final_results = ev.get("results", [])

    st.session_state.sim_results = final_results
    status_box.update(label="Batch Selesai", state="complete", expanded=False)

elif st.session_state.sim_results:
    st.subheader("Hasil Simulasi Terakhir")
    cols = st.columns(2)
    idx_shown = 0
    for item in st.session_state.sim_results:
        if item.get("video_path") and Path(item["video_path"]).exists():
            with cols[idx_shown % 2]:
                st.markdown(f"**{item['name']}** — `{item['status']}`")
                st.caption(item.get("description", ""))
                st.video(item["video_path"])
            idx_shown += 1
        else:
            st.warning(f"{item['name']}: {item['status']}")
