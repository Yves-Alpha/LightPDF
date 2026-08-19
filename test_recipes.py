import unittest

from recipes import DIAPAR_MAX_BYTES, DIAPAR_TARGET_BYTES, get_recipe, respects_size_limit


class RecipeTests(unittest.TestCase):
    def test_front_labels_do_not_expose_engine_vocabulary(self):
        for key in ("original", "screen", "light", "diapar"):
            recipe = get_recipe(key)
            visible_text = f"{recipe.label} {recipe.description}".casefold()
            for forbidden in ("trimbox", "xobject", "cmyk", "qpdf", "ghostscript"):
                self.assertNotIn(forbidden, visible_text)

    def test_diapar_uses_decimal_50_mb_limit_with_headroom(self):
        recipe = get_recipe("diapar")
        self.assertEqual(recipe.max_bytes, DIAPAR_MAX_BYTES)
        self.assertEqual(recipe.target_bytes, DIAPAR_TARGET_BYTES)
        self.assertTrue(respects_size_limit(50_000_000, recipe))
        self.assertFalse(respects_size_limit(50_000_001, recipe))


if __name__ == "__main__":
    unittest.main()
