from __future__ import annotations

import unittest

from naming_rules import analyse_groups, parse_pdf_name


def item(name: str, data: bytes | None = None, page_count: int = 1, folder: str = ""):
    return {
        "name": name,
        "data": data if data is not None else name.encode(),
        "page_count": page_count,
        "folder": folder,
    }


class ParsePdfNameTests(unittest.TestCase):
    def test_page_and_correction_suffixes(self):
        parsed = parse_pdf_name("2614-SUPER SEGU-A4-16P_04_COR.pdf")
        self.assertEqual(parsed.base, "2614-SUPER SEGU-A4-16P")
        self.assertEqual(parsed.page_number, 4)
        self.assertTrue(parsed.corrected)

        spaced = parse_pdf_name("2614-SUPER SEGU-A4-16P_04 COR.PDF")
        self.assertEqual(spaced.page_number, 4)
        self.assertTrue(spaced.corrected)

    def test_processed_suffix_is_identified_without_losing_page(self):
        parsed = parse_pdf_name("Catalogue_012-moyen.pdf")
        self.assertEqual(parsed.base, "Catalogue")
        self.assertEqual(parsed.page_number, 12)
        self.assertEqual(parsed.processed_suffix, "moyen")

        corrected = parse_pdf_name("Catalogue_014_COR-ecran.pdf")
        self.assertEqual(corrected.base, "Catalogue")
        self.assertEqual(corrected.page_number, 14)
        self.assertTrue(corrected.corrected)
        self.assertEqual(corrected.processed_suffix, "ecran")

        reversed_suffixes = parse_pdf_name("Catalogue_014-ecran_COR.pdf")
        self.assertEqual(reversed_suffixes.base, "Catalogue")
        self.assertEqual(reversed_suffixes.page_number, 14)
        self.assertTrue(reversed_suffixes.corrected)
        self.assertEqual(reversed_suffixes.processed_suffix, "ecran")


class GroupingTests(unittest.TestCase):
    def test_pages_are_sorted_numerically_and_correction_wins(self):
        result = analyse_groups(
            [
                item("Catalogue_10.pdf"),
                item("Catalogue_02.pdf", b"original"),
                item("Catalogue_01.pdf"),
                item("Catalogue_02_COR.pdf", b"corrected"),
            ]
        )
        group = result.groups[0]
        self.assertEqual([entry["page_int"] for entry in group.items], [1, 2, 10])
        self.assertEqual(group.items[1]["data"], b"corrected")
        self.assertEqual(group.corrected_pages, [2])
        self.assertEqual(group.discarded[0]["discard_reason"], "remplacé par la correction")

    def test_missing_page_blocks_the_group(self):
        group = analyse_groups([item("Catalogue_01.pdf"), item("Catalogue_03.pdf")]).groups[0]
        self.assertTrue(group.is_blocked)
        self.assertIn("Pages manquantes : 02.", [issue.message for issue in group.issues])

    def test_competing_corrections_block_the_group(self):
        group = analyse_groups(
            [item("Catalogue_04_COR.pdf", b"a"), item("Catalogue_04 cor.pdf", b"b")]
        ).groups[0]
        self.assertTrue(group.is_blocked)
        self.assertIn("multiple_corrections", [issue.code for issue in group.issues])

    def test_numbered_multipage_file_cannot_be_merged_with_components(self):
        group = analyse_groups(
            [item("Catalogue_01.pdf", page_count=16), item("Catalogue_02.pdf")]
        ).groups[0]
        self.assertTrue(group.is_blocked)
        self.assertIn("multipage_component", [issue.code for issue in group.issues])

    def test_complete_document_and_components_are_not_mixed(self):
        group = analyse_groups(
            [item("Catalogue.pdf", page_count=16), item("Catalogue_01.pdf")]
        ).groups[0]
        self.assertTrue(group.is_blocked)
        self.assertIn("mixed_complete_and_parts", [issue.code for issue in group.issues])

    def test_groups_from_different_folders_stay_separate(self):
        result = analyse_groups(
            [item("Catalogue_01.pdf", folder="Client A"), item("Catalogue_02.pdf", folder="Client B")]
        )
        self.assertEqual(len(result.groups), 2)

    def test_hidden_files_are_filtered(self):
        result = analyse_groups([item("._Catalogue_01.pdf"), item("Catalogue_01.pdf")])
        self.assertEqual(len(result.groups), 1)
        self.assertEqual(len(result.ignored), 1)

    def test_generated_delivery_is_filtered_when_source_is_present(self):
        result = analyse_groups(
            [
                item("Catalogue_01-ecran.pdf", b"old"),
                item("Catalogue_01.pdf", b"source"),
            ]
        )

        group = result.groups[0]
        self.assertEqual([entry["name"] for entry in group.items], ["Catalogue_01.pdf"])
        self.assertEqual(group.discarded[0]["discard_reason"], "version déjà préparée")


if __name__ == "__main__":
    unittest.main()
