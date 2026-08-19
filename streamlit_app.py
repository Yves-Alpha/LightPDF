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
    OutputConstraintError,
    clean_pdf,
    vector_compress_pdf,
)
from naming_rules import GroupingResult, analyse_groups, parse_pdf_name  # noqa: E402
from recipes import RECIPES, get_recipe  # noqa: E402


def _init_queue() -> None:
    if "queue" not in st.session_state:
        st.session_state.queue = []  # list of dict{name, data: bytes}
    if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = "pdf_uploader"


def _parse_name(name: str) -> tuple[str, str | None, bool]:
    parsed = parse_pdf_name(name)
    return parsed.base, parsed.page_token, parsed.corrected


def _pdf_page_count(data: bytes) -> int | None:
    try:
        with pikepdf.Pdf.open(BytesIO(data)) as pdf:
            return len(pdf.pages)
    except Exception:
        return None


def _add_item(name: str, data: bytes, folder: str | None = None) -> None:
    _init_queue()
    base, page, is_cor = _parse_name(name)
    folder_key = folder or ""
    # Streamlit returns uploaded files again on every rerun. Keep each exact
    # file once, while retaining original + _COR so the naming engine can show
    # the user which one it selected.
    if any(
        item.get("name") == name and item.get("folder", "") == folder_key
        for item in st.session_state.queue
    ):
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
            "page_count": _pdf_page_count(data),
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
    "DIAPAR": "g20",
}


def _file_suffix(profile: CompressionProfile) -> str:
    return profile.output_suffix or _PROFILE_SUFFIX.get(profile.name, profile.name)


def process_queue(
    output_dir: Path,
    bleed_mm: float,
    profiles: List[CompressionProfile],
    queue: list[dict] | None = None,
) -> list[dict]:
    results = []
    work_queue = queue if queue is not None else list(st.session_state.queue)
    total = len(work_queue)
    progress = st.progress(0.0, text="Démarrage…")

    for idx, item in enumerate(work_queue, start=1):
        name = item["name"]
        data = item["data"]
        progress.progress((idx - 1) / total, text=f"{name} : préparation…")
        base = Path(name).stem
        outputs = []
        errors = []
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_pdf = Path(tmpdir) / Path(name).name
                tmp_pdf.write_bytes(data)
                clean_path = Path(tmpdir) / f"{base}-clean.pdf"
                clean_pdf(tmp_pdf, clean_path, bleed_mm=bleed_mm)
                for profile in profiles:
                    out_pdf = output_dir / f"{base}-{_file_suffix(profile)}.pdf"
                    vector_compress_pdf(clean_path, out_pdf, profile)
                    outputs.append(str(out_pdf))
        except OutputConstraintError as exc:
            errors.append(str(exc))
        except Exception as exc:
            print(f"Échec de {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            errors.append(
                f"{name} n’a pas pu être préparé automatiquement. "
                "Il doit être contrôlé dans Acrobat."
            )
        results.append({"name": base, "outputs": outputs, "errors": errors})
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
        with pikepdf.Pdf.open(BytesIO(item["data"])) as src:
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
    analysis = analyse_groups(queue)
    return {group.output_name: group.items for group in analysis.groups}


def _issue_applies_without_assembly(code: str) -> bool:
    return code in {"multiple_corrections", "conflicting_duplicate"}


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
    st.markdown(
        "Préparez les PDF clients au bon format et au bon poids, "
        "à partir des fichiers HD imprimeur."
    )
    
    # Vérification pikepdf (moteur principal de compression)
    pikepdf_ok = pikepdf is not None

    _init_queue()
    if "uploader_key" not in st.session_state:
        st.session_state["uploader_key"] = f"pdf_uploader_{uuid.uuid4()}"

    with st.sidebar:
        st.header("Votre livraison")
        default_out = Path.home() / "Documents" / "Light-PDF"
        if "out_dir" not in st.session_state:
            st.session_state["out_dir"] = str(default_out)
        if "selected_recipe" not in st.session_state:
            st.session_state["selected_recipe"] = "screen"

        selected_recipe_key = st.radio(
            "Que souhaitez-vous préparer ?",
            options=list(RECIPES),
            format_func=lambda key: RECIPES[key].label,
            key="selected_recipe",
        )
        selected_recipe = get_recipe(selected_recipe_key)
        st.info(selected_recipe.description)

    uploader_key = st.session_state["uploader_key"]
    st.markdown("---")
    uploaded = st.file_uploader("📥 Sélectionnez vos PDFs à optimiser (drag & drop)", type=["pdf"], accept_multiple_files=True, key=uploader_key)
    if uploaded:
        add_to_queue(uploaded)

    st.write(f"Fichiers ajoutés : {len(st.session_state.queue)}")
    if st.button("🗑️ Tout vider"):
        st.session_state.queue = []
        st.session_state.pop("download_items", None)
        st.session_state.uploader_key = f"pdf_uploader_{uuid.uuid4()}"
        st.rerun()

    group_mode = st.checkbox(
        "Assembler automatiquement les pages numérotées",
        value=False,
        help="Rassemble les fichiers _01, _02… et utilise en priorité les versions _COR.",
    )

    grouping: GroupingResult = analyse_groups(st.session_state.queue)
    effective_queue = [item for group in grouping.groups for item in group.items]

    if st.session_state.queue:
        st.markdown("### Organisation détectée")
        for group in grouping.groups:
            page_label = (
                f"{group.total_pages} page(s)"
                if group.total_pages is not None
                else f"{len(group.items)} fichier(s)"
            )
            active_issues = [
                issue
                for issue in group.issues
                if group_mode or _issue_applies_without_assembly(issue.code)
            ]
            status = "⛔" if any(issue.blocking for issue in active_issues) else "✅"
            st.markdown(f"{status} **{group.label}** — {page_label}")
            if group.corrected_pages:
                corrections = ", ".join(f"{page:02d}" for page in group.corrected_pages)
                st.caption(f"Correction retenue pour la ou les page(s) : {corrections}")
            if group.discarded:
                st.caption(f"{len(group.discarded)} doublon(s) ou ancienne(s) version(s) écarté(s).")
            for issue in active_issues:
                if issue.blocking:
                    st.error(issue.message)
                else:
                    st.warning(issue.message)
        if grouping.ignored:
            st.caption(f"{len(grouping.ignored)} fichier(s) temporaire(s) ou non PDF ignoré(s).")

    profiles = [
        CompressionProfile(
            selected_recipe.engine_profile,
            dpi=selected_recipe.dpi,
            quality=selected_recipe.quality,
            max_bytes=selected_recipe.max_bytes,
            target_bytes=selected_recipe.target_bytes,
            output_suffix=selected_recipe.output_suffix,
        )
    ]

    has_outputs = bool(profiles)

    if not pikepdf_ok:
        st.error("⚠️ pikepdf n'est pas disponible. Vérifiez l'installation.")

    unresolved_name_conflict = any(
        issue.blocking and _issue_applies_without_assembly(issue.code)
        for group in grouping.groups
        for issue in group.issues
    )
    start_disabled = (
        (not st.session_state.queue)
        or (not has_outputs)
        or (not pikepdf_ok)
        or unresolved_name_conflict
        or (group_mode and grouping.is_blocked)
    )

    start = st.button(
        "Préparer les fichiers",
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
                    for group in grouping.groups:
                        base_name = group.output_name
                        outputs = []
                        errors = []
                        merged = None
                        tmpdir_merge = None
                        try:
                            merged, tmpdir_merge = merge_queue_into_pdf(group.items, label=base_name)
                            with tempfile.TemporaryDirectory() as tmpclean:
                                clean_path = Path(tmpclean) / f"{base_name}-clean.pdf"
                                clean_pdf(merged, clean_path, bleed_mm=5.0)
                                for profile in profiles:
                                    out_pdf = out_dir / f"{base_name}-{_file_suffix(profile)}.pdf"
                                    vector_compress_pdf(clean_path, out_pdf, profile)
                                    outputs.append(str(out_pdf))
                        except OutputConstraintError as exc:
                            errors.append(str(exc))
                        except Exception as exc:
                            print(
                                f"Échec de {base_name}: {type(exc).__name__}: {exc}",
                                file=sys.stderr,
                            )
                            errors.append(
                                f"{base_name} n’a pas pu être préparé automatiquement. "
                                "Il doit être contrôlé dans Acrobat."
                            )
                        finally:
                            if tmpdir_merge is not None:
                                tmpdir_merge.cleanup()
                        results.append({"name": base_name, "outputs": outputs, "errors": errors})
                    st.session_state.queue = []
                else:
                    results = process_queue(
                        out_dir,
                        bleed_mm=5.0,
                        profiles=profiles,
                        queue=effective_queue,
                    )

            # Stocker les résultats en session pour persistance des téléchargements
            download_items = []
            for res in results:
                for out in res["outputs"]:
                    p = Path(out)
                    if p.exists():
                        download_items.append({"name": p.name, "data": p.read_bytes()})
            st.session_state["download_items"] = download_items
            st.session_state["processing_errors"] = [
                error for result in results for error in result.get("errors", [])
            ]

    # ── Section téléchargement (persiste entre les reruns Streamlit) ──
    if st.session_state.get("download_items"):
        st.success("✅ Fichiers prêts !")
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
            st.session_state.pop("processing_errors", None)
            st.rerun()

    if st.session_state.get("processing_errors"):
        st.warning("Certains fichiers nécessitent une reprise manuelle.")
        for message in st.session_state["processing_errors"]:
            st.write(f"• {message}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        st.error("Le traitement s’est interrompu. Rechargez la page puis réessayez.")
        print(f"Erreur critique {type(e).__name__}: {e}", file=sys.stderr)
