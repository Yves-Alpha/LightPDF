"""User-facing delivery recipes and their non-negotiable constraints."""

from __future__ import annotations

from dataclasses import dataclass


DIAPAR_MAX_BYTES = 50_000_000
DIAPAR_TARGET_BYTES = 48_000_000


@dataclass(frozen=True)
class DeliveryRecipe:
    key: str
    label: str
    description: str
    engine_profile: str
    output_suffix: str
    dpi: int = 0
    quality: int = 0
    max_bytes: int | None = None
    target_bytes: int | None = None


RECIPES: dict[str, DeliveryRecipe] = {
    "original": DeliveryRecipe(
        key="original",
        label="Qualité d’origine",
        description="Retire les traits de coupe et marges imprimeur, sans alléger le document.",
        engine_profile="Nettoyer",
        output_suffix="format-final",
    ),
    "screen": DeliveryRecipe(
        key="screen",
        label="Version écran — recommandée",
        description="Prépare un PDF plus compact en conservant le texte net et sélectionnable.",
        engine_profile="Moyen",
        output_suffix="ecran",
    ),
    "light": DeliveryRecipe(
        key="light",
        label="Version très légère",
        description="Privilégie un poids minimal pour la consultation en ligne.",
        engine_profile="Très légers",
        output_suffix="leger",
        dpi=150,
        quality=75,
    ),
    "diapar": DeliveryRecipe(
        key="diapar",
        label="Intranet G20 — moins de 50 Mo",
        description="Prépare une version pour DIAPAR et bloque tout fichier dépassant la limite.",
        engine_profile="DIAPAR",
        output_suffix="g20",
        max_bytes=DIAPAR_MAX_BYTES,
        target_bytes=DIAPAR_TARGET_BYTES,
    ),
}


def get_recipe(key: str) -> DeliveryRecipe:
    try:
        return RECIPES[key]
    except KeyError as exc:
        raise ValueError(f"Recette inconnue : {key}") from exc


def respects_size_limit(size_bytes: int, recipe: DeliveryRecipe) -> bool:
    return recipe.max_bytes is None or size_bytes <= recipe.max_bytes
