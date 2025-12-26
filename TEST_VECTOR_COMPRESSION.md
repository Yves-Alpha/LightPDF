# Solution : Compression Vectorielle Sans Pixellisation

## 🎯 Problème Résolu

L'application produisait des PDFs pixellisés car elle **rasterisait** (convertissait en image) chaque page. 

## ✅ Solution Implémentée

### 1. **Nouvelle fonction `vector_compress_pdf()`** dans `app.py`
- Utilise **Ghostscript** pour compresser les PDF
- Conserve **texte et éléments vectoriels nets**
- Pas de pixellisation
- Paramètres Ghostscript optimisés :
  - `-dPDFSETTINGS=/presetDefault` → compression intelligente
  - `-dDetectDuplicateImages=true` → réduit les doublons
  - `-dCompressFonts=true` → compresse les polices
  - `-dSubsetFonts=true` → optimise les polices

### 2. **Modification de `CompressionProfile`**
```python
@dataclass
class CompressionProfile:
    name: str
    dpi: int
    quality: int  
    use_vector_compression: bool = False  # ← NOUVEAU
```

### 3. **Nouveau profil dans l'UI**
**"Haute Qualité - Vectoriel (Recommandé !)"**
- Activé dans la section "📊 Compression Vectorielle"
- Conserve texte et vecteurs nets
- DPI configurable pour les images embarquées
- Qualité contrôlable (10-100)

## 📋 Utilisation

### Dans Streamlit
1. Aller à la barre latérale
2. Activer **"Haute Qualité - Vectoriel"** (⭐ recommandé)
3. Ajuster le DPI et la qualité si désiré
4. S'assurer que **Ghostscript est installé** (`brew install ghostscript`)
5. Charger les PDFs et lancer la conversion

### En CLI (Python)
```python
from LightPDF.app import CompressionProfile, vector_compress_pdf
from pathlib import Path

profile = CompressionProfile(
    name="Vector-HQ",
    dpi=300,
    quality=92,
    use_vector_compression=True
)

vector_compress_pdf(
    Path("input.pdf"),
    Path("output.pdf"),
    profile
)
```

## 🔧 Dépendances Requises

- **Ghostscript** : `brew install ghostscript`
- PyPDF2, pdf2image, reportlab, Pillow (déjà dans requirements)

## 📊 Comparaison

| Méthode | Texte | Vecteurs | Taille | Qualité |
|---------|-------|----------|--------|---------|
| Rasterisé (ancien) | ❌ Pixellisé | ❌ Pixellisé | Gros | Moyenne |
| **Vectoriel (NOUVEAU)** | ✅ Net | ✅ Net | Petit | Haute |
| Aplat (intermédiaire) | ✅ Net | ✅ Net | Moyen | Très haute |

## 🚀 Améliorations

- ✅ Trois profils de sortie au lieu de deux
- ✅ Choix intelligent entre rasterisation et compression vectorielle
- ✅ Interface claire et intuitive
- ✅ Gestion des dépendances automatique
- ✅ Compatible avec le mode groupement

## ⚙️ Configuration Fine (Experts)

Dans `vector_compress_pdf()`, vous pouvez ajuster :
- `/presetDefault` → `ebook`, `screen`, `printer`, `presetDefault`
- `-dColorImageResolution=150` → résolution pour images
- `-dDownsampleColorImages=true` → sous-échantillonner les images

