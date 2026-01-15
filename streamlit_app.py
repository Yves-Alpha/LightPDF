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
from PyPDF2 import PdfReader, PdfWriter  # pyright: ignore[reportMissingImports]

# Ensure Application Support path uses the Light-PDF app name
os.environ.setdefault("ROTO_APP_NAME", "Light-PDF")

ROOT_DIR = Path(__file__).resolve().parent
FAVICON = ROOT_DIR / "icone-Light-PDF.png"
sys.path.insert(0, str(ROOT_DIR))

from app import (  # noqa: E402
    CompressionProfile,
    clean_pdf,
    compress_images_only_pdf,
    flatten_transparency_pdf,
    find_qpdf,
    has_ghostscript,
    raster_compress_pdf,
    vector_compress_pdf,
    warn_pdftoppm,
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


def process_queue(
    output_dir: Path,
    bleed_mm: float,
    profiles: List[CompressionProfile],
    flatten: bool,
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
            if flatten:
                flat_pdf = output_dir / f"{base}.pdf"
                try:
                    method = flatten_transparency_pdf(clean_path, flat_pdf)
                    outputs.append(str(flat_pdf))
                    if method != "gs compat 1.3":
                        st.warning(f"{name} : aplat vectoriel via '{method}' (fallback). Vérifier le rendu.")
                except Exception as exc:  # pragma: no cover - UI feedback path
                    st.error(f"{name} : échec de l'aplat vectoriel ({exc})")
            for profile in profiles:
                out_pdf = output_dir / f"{base}-{profile.name}.pdf"
                if profile.image_only:
                    compress_images_only_pdf(clean_path, out_pdf, profile)
                elif profile.use_vector_compression:
                    vector_compress_pdf(clean_path, out_pdf, profile)
                else:
                    raster_compress_pdf(clean_path, out_pdf, profile)
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
    writer = PdfWriter()
    for item in sorted(
        queue,
        key=lambda x: (
            x.get("page_int") is None,
            x.get("page_int") or 0,
            x["name"],
        ),
    ):
        reader = PdfReader(BytesIO(item["data"]))
        for page in reader.pages:
            writer.add_page(page)
    with open(merged_path, "wb") as f:
        writer.write(f)
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
    
    # Détection des dépendances (résultats cachés)
    try:
        ghostscript_ok = has_ghostscript()
    except Exception as e:
        print(f"[ERROR] has_ghostscript() failed: {e}", flush=True)
        ghostscript_ok = False
    
    try:
        poppler_ok = has_pdftoppm()
    except Exception as e:
        print(f"[ERROR] has_pdftoppm() failed: {e}", flush=True)
        poppler_ok = False

    try:
        qpdf_ok = find_qpdf() is not None
    except Exception as e:
        print(f"[ERROR] find_qpdf() failed: {e}", flush=True)
        qpdf_ok = False

    _init_queue()
    if "flatten_enabled" not in st.session_state:
        st.session_state["flatten_enabled"] = False
    if "uploader_key" not in st.session_state:
        st.session_state["uploader_key"] = f"pdf_uploader_{uuid.uuid4()}"

    with st.sidebar:
        st.header("Options")
        default_out = Path.home() / "Documents" / "Light-PDF"
        if "out_dir" not in st.session_state:
            st.session_state["out_dir"] = str(default_out)
        if "profiles" not in st.session_state:
            st.session_state["profiles"] = {
                "hq": {"enabled": False, "dpi": 300, "q": 92, "vector": False},
                "lite": {"enabled": False, "dpi": 150, "q": 78, "vector": False},
                "vector_mix": {"enabled": True, "dpi": 150, "q": 75, "vector": True},
            }
        
        # Section de sélection du dossier de destination
        st.subheader("⚙️ Paramètres")
        bleed_mm = st.number_input("🔲 Marge de sécurité à retirer (mm)", value=5.0, min_value=0.0, step=0.5, help="Espace blanc autour de votre document à supprimer (traits de coupe, etc.)")
        st.subheader("🎯 Choisissez votre profil")
        
        # Initialiser la sélection de profil s'il n'existe pas
        if "selected_profile" not in st.session_state:
            st.session_state["selected_profile"] = "vector_mix"
        
        # Radio button pour la sélection unique
        selected = st.radio(
            "Sélectionnez un seul profil :",
            options=["vector_mix", "lite", "hq"],
            format_func=lambda x: {
                "vector_mix": "🪶 Vecteurs nets + images léger",
                "lite": "💾 Très léger – Poids réduit au maximum",
                "hq": "⚙️ Impression professionnelle – Meilleure qualité (lourd)"
            }[x],
            key="selected_profile"
        )
        
        # Mettre à jour les profils : désactiver les autres, activer le sélectionné
        st.session_state["profiles"]["vector_mix"]["enabled"] = (selected == "vector_mix")
        st.session_state["profiles"]["lite"]["enabled"] = (selected == "lite")
        st.session_state["profiles"]["hq"]["enabled"] = (selected == "hq")
        
        # Afficher les options du profil sélectionné
        if selected == "vector_mix":
            col1, col2 = st.columns(2)
            with col1:
                st.session_state.profiles["vector_mix"]["dpi"] = st.slider(
                    "Résolution des images (DPI)",
                    min_value=72, max_value=300,
                    value=st.session_state.profiles["vector_mix"]["dpi"],
                    key="vector_mix_dpi",
                    help="Agit uniquement sur les images raster (pas sur les vecteurs)."
                )
            with col2:
                st.session_state.profiles["vector_mix"]["q"] = st.slider(
                    "Compression",
                    min_value=10, max_value=100,
                    value=st.session_state.profiles["vector_mix"]["q"],
                    key="vector_mix_q",
                    help="Qualité JPEG des images raster uniquement."
                )
            st.info("✨ Vecteurs préservés intactes. Les images raster sont compressées selon DPI/Qualité.")
        
        elif selected == "lite":
            lite_state = st.session_state["profiles"]["lite"]
            col1, col2 = st.columns(2)
            with col1:
                st.session_state.profiles["lite"]["dpi"] = st.slider(
                    "Résolution des images",
                    min_value=72, max_value=300, 
                    value=st.session_state.profiles["lite"]["dpi"],
                    key="lite_dpi",
                    help="72 DPI = très léger | 150 DPI = acceptable"
                )
            with col2:
                st.session_state.profiles["lite"]["q"] = st.slider(
                    "Compression",
                    min_value=10, max_value=100, 
                    value=st.session_state.profiles["lite"]["q"],
                    key="lite_q",
                    help="10-40 = très compressé | 50-70 = équilibré"
                )
        
        elif selected == "hq":
            hq_state = st.session_state["profiles"]["hq"]
            st.write("**Profil haute qualité**")
            st.session_state.profiles["hq"]["dpi"] = st.slider(
                "Résolution des images",
                min_value=72, max_value=300, 
                value=st.session_state.profiles["hq"]["dpi"],
                key="hq_dpi"
            )
            st.session_state.profiles["hq"]["q"] = st.slider(
                "Compression",
                min_value=10, max_value=100, 
                value=st.session_state.profiles["hq"]["q"],
                key="hq_q"
            )
        
        st.markdown("---")
        
        # Options avancées (cachées)
        with st.expander("⚙️ Options spéciales"):
            
            st.markdown("---")
            st.write("**Options spéciales**")
            flat_enabled = st.checkbox(
                "🔄 Aplatir les transparences (sans pixellisation)",
                value=st.session_state["flatten_enabled"],
                help="Fusionne les calques de transparence tout en conservant le texte et vecteurs nets.",
            )
            st.session_state["flatten_enabled"] = flat_enabled
            if flat_enabled and not ghostscript_ok:
                st.warning("⚠️ Nécessite Ghostscript. Installez via : `brew install ghostscript`")

    uploader_key = st.session_state["uploader_key"]
    st.markdown("---")
    uploaded = st.file_uploader("📥 Sélectionnez vos PDFs à optimiser (drag & drop)", type=["pdf"], accept_multiple_files=True, key=uploader_key)
    if uploaded:
        add_to_queue(uploaded)

    st.write(f"File d'attente : {len(st.session_state.queue)} fichier(s)")
    if st.button("🗑️ Tout vider"):
        st.session_state.queue = []
        st.session_state.uploader_key = f"pdf_uploader_{uuid.uuid4()}"
        st.rerun()

    group_mode = st.checkbox("🔗 Regrouper les PDFs par nom (_01, _02… fusionnés)", value=False, help="Fusionne les pages numérotées en un seul PDF")

    # Build selected profiles
    profiles = []
    prof_state = st.session_state.get("profiles", {})
    if prof_state.get("hq", {}).get("enabled"):
        profiles.append(
            CompressionProfile("HQ", dpi=int(prof_state["hq"]["dpi"]), quality=int(prof_state["hq"]["q"]), use_vector_compression=False)
        )
    if prof_state.get("lite", {}).get("enabled"):
        profiles.append(
            CompressionProfile("Light", dpi=int(prof_state["lite"]["dpi"]), quality=int(prof_state["lite"]["q"]), use_vector_compression=False)
        )
    if prof_state.get("vector_mix", {}).get("enabled"):
        profiles.append(
            CompressionProfile("Vector-IMG", dpi=int(prof_state["vector_mix"]["dpi"]), quality=int(prof_state["vector_mix"]["q"]), use_vector_compression=True, image_only=True)
        )
    
    # Debug: afficher les profils construits
    if profiles:
        st.write("**Profils sélectionnés :**")
        for p in profiles:
            st.write(f"- {p.name}: DPI={p.dpi}, Quality={p.quality}, Vector={p.use_vector_compression}")

    flatten_enabled = bool(st.session_state.get("flatten_enabled", False))
    needs_poppler = any(not p.use_vector_compression for p in profiles)
    needs_ghostscript = flatten_enabled
    has_outputs = bool(profiles) or flatten_enabled

    if not poppler_ok and needs_poppler:
        st.error("Poppler/pdftoppm n'est pas installé. Requis pour HQ/Light. Sur Streamlit Cloud, vérifie `packages.txt` (poppler-utils) puis redeploie.")
    elif not poppler_ok and not needs_poppler:
        st.info("Poppler/pdftoppm n'est pas installé. Active un profil HQ/Light après installation (brew install poppler ou `packages.txt` → poppler-utils).")
    if needs_ghostscript and not ghostscript_ok:
        st.error("Ghostscript (gs) est requis pour l'option vectorielle. Sur Streamlit Cloud, ajoute `ghostscript` dans `packages.txt` puis redeploie (déjà présent si repo à jour).")

    start_disabled = (
        (not st.session_state.queue)
        or (needs_poppler and not poppler_ok)
        or (needs_ghostscript and not ghostscript_ok)
        or (not has_outputs)
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
                            clean_pdf(merged, clean_path, bleed_mm=bleed_mm)
                            outputs = []
                            if flatten_enabled:
                                flat_out = out_dir / f"{base_name}.pdf"
                                try:
                                    method = flatten_transparency_pdf(clean_path, flat_out)
                                    outputs.append(str(flat_out))
                                    if method != "gs compat 1.3":
                                        st.warning(f"{base_name} : aplat vectoriel via '{method}' (fallback). Vérifier le rendu.")
                                except Exception as exc:  # pragma: no cover - UI feedback path
                                    st.error(f"{base_name} : échec de l'aplat vectoriel ({exc})")
                            for profile in profiles:
                                out_pdf = out_dir / f"{base_name}-{profile.name}.pdf"
                                if profile.image_only:
                                    compress_images_only_pdf(clean_path, out_pdf, profile)
                                elif profile.use_vector_compression:
                                    vector_compress_pdf(clean_path, out_pdf, profile)
                                else:
                                    raster_compress_pdf(clean_path, out_pdf, profile)
                                outputs.append(str(out_pdf))
                            results.append({"name": base_name, "outputs": outputs})
                        tmpdir_merge.cleanup()
                    st.session_state.queue = []
                else:
                    results = process_queue(out_dir, bleed_mm=bleed_mm, profiles=profiles, flatten=flatten_enabled)
            
            st.success("✅ Optimisation terminée !")
            
            # Téléchargement des fichiers générés
            generated_paths = []
            for res in results:
                for out in res["outputs"]:
                    p = Path(out)
                    if p.exists():
                        generated_paths.append(p)
            
            if generated_paths:
                st.markdown("### ⬇️ Téléchargement")
                
                if len(generated_paths) > 1:
                    # Créer un ZIP avec tous les fichiers
                    zip_buf = BytesIO()
                    with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                        for p in generated_paths:
                            zf.write(p, arcname=p.name)
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
                
                # Téléchargements individuels
                for idx, p in enumerate(generated_paths):
                    st.download_button(
                        f"📄 {p.name}",
                        data=p.read_bytes(),
                        file_name=p.name,
                        mime="application/pdf",
                        use_container_width=True,
                        key=f"download_{idx}_{p.name}"
                    )
            else:
                st.info("Aucun fichier généré à proposer en téléchargement.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        st.error(f"❌ Erreur critique: {type(e).__name__}: {e}")
        import traceback
        st.error(traceback.format_exc())
