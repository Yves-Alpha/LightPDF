#!/usr/bin/env python3
"""
Streamlit UI for Light-PDF: drop multiple PDFs, pick an output folder, and convert.
Run with:
    streamlit run LightPDF/streamlit_app.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import subprocess
import shutil
import re
import uuid
import zipfile
from pathlib import Path
from typing import List
from io import BytesIO

import streamlit as st  # pyright: ignore[reportMissingImports]
import pikepdf  # pyright: ignore[reportMissingImports]

# Ensure Application Support path uses the Light-PDF app name
os.environ.setdefault("ROTO_APP_NAME", "Light-PDF")

ROOT_DIR = Path(__file__).resolve().parent
FAVICON = ROOT_DIR / "icone-Light-PDF.png"
sys.path.insert(0, str(ROOT_DIR))

from app import (  # noqa: E402
    CompressionProfile,
    clean_pdf,
    find_qpdf,
    vector_compress_pdf,
)


def _init_queue() -> None:
    if "queue" not in st.session_state:
        st.session_state.queue = []  # list of dict{name, data: bytes}
    if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = "pdf_uploader"


def _parse_name(name: str) -> tuple[str, str | None, bool]:
    stem = Path(name).stem
    m = re.match(r"^(?P<base>.*?)(?:_(?P<page>\d{2}))?(?P<cor>[_ ]cor)?$", stem, flags=re.IGNORECASE)
    if not m:
        return stem, None, False
    return m.group("base").rstrip(), m.group("page"), bool(m.group("cor"))


def _add_item(name: str, data: bytes, folder: str | None = None) -> None:
    _init_queue()
    base, page, is_cor = _parse_name(name)
    folder_key = folder or ""
    # check existing same base/page/folder
    existing_idx = None
    cor_present = False
    for idx, item in enumerate(st.session_state.queue):
        if item.get("base") == base and item.get("page") == page and item.get("folder") == folder_key:
            existing_idx = idx
            cor_present = cor_present or item.get("corrected", False)
            break
    if is_cor and existing_idx is not None and not st.session_state.queue[existing_idx].get("corrected"):
        st.session_state.queue.pop(existing_idx)
        existing_idx = None
    if (not is_cor) and cor_present:
        return
    if existing_idx is not None:
        return
    st.session_state.queue.append(
        {
            "name": name,
            "data": data,
            "folder": folder_key,
            "base": base,
            "page": page,
            "page_int": int(page) if page else None,
            "corrected": is_cor,
        }
    )


def add_to_queue(files) -> None:
    _init_queue()
    for f in files:
        _add_item(f.name, f.getvalue())
    stems = [Path(item["name"]).stem for item in st.session_state.queue]
    st.session_state["group_label"] = _common_prefix(stems) or "regroupe"


def add_folder_to_queue(folder: Path) -> None:
    pdfs = sorted([p for p in folder.iterdir() if p.suffix.lower() == ".pdf"])
    if not pdfs:
        st.warning("Aucun PDF trouvé dans ce dossier.")
        return
    _init_queue()
    for p in pdfs:
        _add_item(p.name, p.read_bytes(), folder=folder.name)
    st.session_state["group_label"] = folder.name


# Suffixe propre pour les noms de fichiers (sans accents ni espaces)
_PROFILE_SUFFIX = {
    "Nettoyer": "net",
    "Moyen": "moyen",
    "Très légers": "leger",
}


def _file_suffix(profile: CompressionProfile) -> str:
    return _PROFILE_SUFFIX.get(profile.name, profile.name)


def process_queue(
    output_dir: Path,
    bleed_mm: float,
    profiles: List[CompressionProfile],
) -> list[dict]:
    results = []
    total = len(st.session_state.queue)
    progress = st.progress(0.0, text="Démarrage…")

    for idx, item in enumerate(list(st.session_state.queue), start=1):
        name = item["name"]
        data = item["data"]
        progress.progress((idx - 1) / total, text=f"{name} : préparation…")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_pdf = Path(tmpdir) / name
            tmp_pdf.write_bytes(data)
            base = Path(name).stem
            clean_path = Path(tmpdir) / f"{base}-clean.pdf"
            clean_pdf(tmp_pdf, clean_path, bleed_mm=bleed_mm)
            outputs = []
            for profile in profiles:
                out_pdf = output_dir / f"{base}-{_file_suffix(profile)}.pdf"
                vector_compress_pdf(clean_path, out_pdf, profile)
                outputs.append(str(out_pdf))
            results.append({"name": base, "outputs": outputs})
        progress.progress(idx / total, text=f"{name} : terminé ({idx}/{total})")

    progress.progress(1.0, text="Conversion terminée.")
    # Clear queue after processing
    st.session_state.queue = []
    return results


def merge_queue_into_pdf(queue, label: str | None = None) -> tuple[Path, tempfile.TemporaryDirectory]:
    tmpdir = tempfile.TemporaryDirectory()
    merged_path = Path(tmpdir.name) / (f"{label}.pdf" if label else "merged.pdf")
    merged_pdf = pikepdf.Pdf.new()
    for item in sorted(
        queue,
        key=lambda x: (
            x.get("page_int") is None,
            x.get("page_int") or 0,
            x["name"],
        ),
    ):
        src = pikepdf.Pdf.open(BytesIO(item["data"]))
        merged_pdf.pages.extend(src.pages)
    merged_pdf.save(merged_path)
    merged_pdf.close()
    return merged_path, tmpdir


def _common_prefix(stems: list[str]) -> str:
    if not stems:
        return ""
    prefix = stems[0]
    for s in stems[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix


def group_by_basename(queue: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, dict[str, dict]] = {}
    for item in queue:
        core, page, corrected = _parse_name(item["name"])
        folder = item.get("folder") or ""
        key = f"{folder}/{core}" if folder else core
        entry: dict[str, dict] = groups.setdefault(key, {})
        page_key = page or core  # fallback to core for ordering

        # Prepare a copy with metadata
        copy = dict(item)
        copy["page"] = page
        copy["page_int"] = int(page) if page else None
        copy["corrected"] = corrected

        existing = entry.get(page_key)
        if existing:
            # If corrected version exists, replace the original
            if corrected and not existing.get("corrected"):
                entry[page_key] = copy
            # If existing is corrected, ignore non-corrected duplicates
            else:
                continue
        else:
            entry[page_key] = copy

    # Convert map to sorted lists per group
    result: dict[str, list[dict]] = {}
    for key, items_map in groups.items():
        items = list(items_map.values()) if isinstance(items_map, dict) else items_map
        items_sorted = sorted(
            items,
            key=lambda x: (
                x.get("page_int") is None,
                x.get("page_int") or 0,
                x["name"],
            ),
        )
        result[key] = items_sorted
    return result


def has_pdftoppm() -> bool:
    # Try PATH, then common Homebrew locations
    candidates = [
        shutil.which("pdftoppm"),
        "/usr/bin/pdftoppm",  # Debian/Ubuntu (Streamlit Cloud)
        "/opt/homebrew/bin/pdftoppm",
        "/usr/local/bin/pdftoppm",
    ]
    return any(p and Path(p).exists() for p in candidates)


def choose_folder_via_finder(default_path: Path) -> Path | None:
    if sys.platform != "darwin":
        st.warning("Le sélecteur Finder est disponible uniquement sur macOS.")
        return None
    if not default_path.exists():
        default_path = Path.home()
    prompt = "Choisissez le dossier de sortie"
    base = str(default_path).replace('"', '\\"')
    script = f'''
        set defaultFolder to POSIX file "{base}"
        set theFolder to choose folder with prompt "{prompt}" default location defaultFolder
        POSIX path of theFolder
    '''
    res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if res.returncode == 0:
        selected = res.stdout.strip()
        if selected:
            return Path(selected)
        st.error("Sélection annulée ou dossier invalide.")
    else:
        st.error(f"Impossible d'ouvrir le sélecteur Finder. Détail: {res.stderr.strip() or res.stdout.strip()}")
    return None


def main() -> None:
    page_icon = str(FAVICON) if FAVICON.exists() else "📄"
    # set_page_config doit être appelé avant toute commande Streamlit
    st.set_page_config(page_title="Light PDF", page_icon=page_icon, layout="wide")
    st.title("🪶 Light-PDF")
    st.markdown("Optimisez vos PDFs : **sans pixellisation du texte et des images**, traits de coupe et fonds perdus supprimés.")
    
    # Vérification pikepdf (moteur principal de compression)
    pikepdf_ok = pikepdf is not None

    _init_queue()
    if "uploader_key" not in st.session_state:
        st.session_state["uploader_key"] = f"pdf_uploader_{uuid.uuid4()}"

    with st.sidebar:
        st.header("Options")
        default_out = Path.home() / "Documents" / "Light-PDF"
        if "out_dir" not in st.session_state:
            st.session_state["out_dir"] = str(default_out)
        if "profiles" not in st.session_state:
            st.session_state["profiles"] = {
                "clean": {"enabled": False},
                "medium": {"enabled": True},
                "lite": {"enabled": False},
            }
        
        # Section de sélection du dossier de destination
        st.subheader("⚙️ Paramètres")
        st.subheader("🎯 Choisissez votre profil")
        
        # Initialiser la sélection de profil s'il n'existe pas
        if "selected_profile" not in st.session_state:
            st.session_state["selected_profile"] = "medium"
        
        # Radio button pour la sélection unique
        selected = st.radio(
            "Sélectionnez un profil :",
            options=["clean", "medium", "lite"],
            format_func=lambda x: {
                "clean": "🧹 Au format – Supprime fonds perdus, qualité intacte",
                "medium": "⚖️ Moyen – Bon compromis poids/qualité",
                "lite": "💾 Très légers – Maximum compression",
            }[x],
            key="selected_profile"
        )
        
        # Mettre à jour les profils : désactiver les autres, activer le sélectionné
        st.session_state["profiles"]["clean"]["enabled"] = (selected == "clean")
        st.session_state["profiles"]["medium"]["enabled"] = (selected == "medium")
        st.session_state["profiles"]["lite"]["enabled"] = (selected == "lite")
        
        # Afficher les options du profil sélectionné
        if selected == "clean":
            st.info("Supprime les fonds perdus, qualité intacte.")
        
        elif selected == "medium":
            st.info("Bon compromis : fichier plus léger, sans défauts.")
        
        elif selected == "lite":
            st.info("Très léger, mais avec pixellation visuelle.")
        
        st.markdown("---")

    uploader_key = st.session_state["uploader_key"]
    st.markdown("---")
    uploaded = st.file_uploader("📥 Sélectionnez vos PDFs à optimiser (drag & drop)", type=["pdf"], accept_multiple_files=True, key=uploader_key)
    if uploaded:
        add_to_queue(uploaded)

    st.write(f"File d'attente : {len(st.session_state.queue)} fichier(s)")
    if st.button("🗑️ Tout vider"):
        st.session_state.queue = []
        st.session_state.pop("download_items", None)
        st.session_state.uploader_key = f"pdf_uploader_{uuid.uuid4()}"
        st.rerun()

    group_mode = st.checkbox("🔗 Regrouper les PDFs par nom (_01, _02… fusionnés)", value=False, help="Fusionne les pages numérotées en un seul PDF")

    # Build selected profiles
    profiles = []
    prof_state = st.session_state.get("profiles", {})
    if prof_state.get("clean", {}).get("enabled"):
        profiles.append(
            CompressionProfile("Nettoyer", dpi=0, quality=0)
        )
    if prof_state.get("medium", {}).get("enabled"):
        profiles.append(
            CompressionProfile("Moyen", dpi=0, quality=0)
        )
    if prof_state.get("lite", {}).get("enabled"):
        # Très légers: pikepdf recompression q30, scale 35%
        profiles.append(
            CompressionProfile("Très légers", dpi=0, quality=30)
        )
    
    # Debug: afficher les profils construits
    if profiles:
        st.write("**Profil(s) sélectionné(s) :**")
        for p in profiles:
            if p.name == "Nettoyer":
                st.write(f"- 🧹 {p.name} : supprime fonds perdus, qualité intacte")
            elif p.name == "Moyen":
                st.write(f"- ⚖️ {p.name} : compression modérée (qualité 55, échelle 70%)")
            else:
                st.write(f"- 💾 {p.name} : compression maximale (qualité 30, échelle 35%)")

    has_outputs = bool(profiles)

    if not pikepdf_ok:
        st.error("⚠️ pikepdf n'est pas disponible. Vérifiez l'installation.")

    start_disabled = (
        (not st.session_state.queue)
        or (not has_outputs)
        or (not pikepdf_ok)
    )

    start = st.button(
        "🚀 Lancer l'optimisation",
        type="primary",
        disabled=start_disabled,
    )

    if start:
        # Utiliser un dossier temporaire pour la génération des fichiers
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            with st.spinner("⏳ Optimisation en cours…"):
                if group_mode:
                    results = []
                    groups = group_by_basename(st.session_state.queue)
                    for key, items in groups.items():
                        merged, tmpdir_merge = merge_queue_into_pdf(items, label=key)
                        base_name = key.replace("/", "_")
                        with tempfile.TemporaryDirectory() as tmpclean:
                            clean_path = Path(tmpclean) / f"{base_name}-clean.pdf"
                            clean_pdf(merged, clean_path, bleed_mm=5.0)
                            outputs = []
                            for profile in profiles:
                                out_pdf = out_dir / f"{base_name}-{_file_suffix(profile)}.pdf"
                                vector_compress_pdf(clean_path, out_pdf, profile)
                                outputs.append(str(out_pdf))
                            results.append({"name": base_name, "outputs": outputs})
                        tmpdir_merge.cleanup()
                    st.session_state.queue = []
                else:
                    results = process_queue(out_dir, bleed_mm=5.0, profiles=profiles)

            # Stocker les résultats en session pour persistance des téléchargements
            download_items = []
            for res in results:
                for out in res["outputs"]:
                    p = Path(out)
                    if p.exists():
                        download_items.append({"name": p.name, "data": p.read_bytes()})
            st.session_state["download_items"] = download_items

    # ── Section téléchargement (persiste entre les reruns Streamlit) ──
    if st.session_state.get("download_items"):
        st.success("✅ Optimisation terminée !")
        st.markdown("### ⬇️ Téléchargement")
        items = st.session_state["download_items"]

        if len(items) > 1:
            zip_buf = BytesIO()
            with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for item in items:
                    zf.writestr(item["name"], item["data"])
            zip_buf.seek(0)
            st.download_button(
                "📦 Télécharger tous les fichiers (ZIP)",
                data=zip_buf,
                file_name="LightPDF_outputs.zip",
                mime="application/zip",
                use_container_width=True,
                key="download_all_zip"
            )
            st.write("---")

        for idx, item in enumerate(items):
            st.download_button(
                f"📄 {item['name']}",
                data=item["data"],
                file_name=item["name"],
                mime="application/pdf",
                use_container_width=True,
                key=f"download_{idx}_{item['name']}"
            )

        if st.button("🗑️ Effacer les résultats"):
            del st.session_state["download_items"]
            st.rerun()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        st.error(f"❌ Erreur critique: {type(e).__name__}: {e}")
        import traceback
        st.error(traceback.format_exc())
