import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from slides_to_video import (  # noqa: E402
    extract_narrations_from_tex,
    get_page_plan,
    write_narrations_to_files,
)


class PhysicalPageNarrationTest(unittest.TestCase):
    annotations = (
        "Title narration",
        "Main section narration",
        "Main frame narration",
        "Appendix section narration",
        "Appendix frame narration",
    )

    def _write_fixture(self, root: Path, annotations: tuple[str, ...]) -> Path:
        def annotation(index: int) -> list[str]:
            if index >= len(annotations):
                return []
            return [f"% NARRATION: {annotations[index]}"]

        lines = [
            r"\documentclass{ctexbeamer}",
            r"\usetheme{sustech}",
            r"\begin{document}",
            r"\begin{frame}{Title}",
            r"\titlepage",
            r"\end{frame}",
            *annotation(0),
            *annotation(1),
            r"\section{Main}",
            r"\begin{frame}{Main frame}",
            "Main content",
            r"\end{frame}",
            *annotation(2),
            r"\appendix",
            *annotation(3),
            r"\section{Appendix}",
            r"\begin{frame}{Appendix frame}",
            "Appendix content",
            r"\end{frame}",
            *annotation(4),
            *[f"% NARRATION: {text}" for text in annotations[5:]],
            r"\end{document}",
        ]
        tex_path = root / "fixture.tex"
        tex_path.write_text("\n".join(lines), encoding="utf-8")
        return tex_path

    def _assert_invalid(self, annotations: tuple[str, ...], message: str) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            tex_path = self._write_fixture(root, annotations)
            with self.assertRaisesRegex(ValueError, message):
                extract_narrations_from_tex(str(tex_path))
            with self.assertRaisesRegex(ValueError, message):
                write_narrations_to_files(str(root / "frames"), str(tex_path))

    def test_five_physical_pages_are_ordered_in_plan_txt_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            tex_path = self._write_fixture(root, self.annotations)
            frame_dir = root / "frames"
            frame_dir.mkdir()
            for stale_name in (
                "slide_999.txt",
                "slide_999.mp3",
                "slide_999_final.mp4",
            ):
                (frame_dir / stale_name).write_text("stale", encoding="utf-8")

            self.assertEqual(
                get_page_plan(str(tex_path)),
                [
                    {"type": "frame", "frame_idx": 1, "title": "Title"},
                    {"type": "section", "section_name": "Main"},
                    {"type": "frame", "frame_idx": 2, "title": "Main frame"},
                    {"type": "section", "section_name": "Appendix"},
                    {"type": "frame", "frame_idx": 3, "title": "Appendix frame"},
                ],
            )

            result = write_narrations_to_files(
                str(frame_dir), str(tex_path)
            )
            self.assertEqual(result["count"], 5)
            self.assertEqual(result["frame_count"], 3)
            self.assertEqual(result["section_pages"], [2, 4])
            self.assertEqual(result["files"], [1, 2, 3, 4, 5])
            self.assertEqual(
                [
                    (frame_dir / f"slide_{page:03d}.txt").read_text(encoding="utf-8")
                    for page in range(1, 6)
                ],
                list(self.annotations),
            )
            self.assertFalse((frame_dir / "slide_999.txt").exists())
            self.assertFalse((frame_dir / "slide_999.mp3").exists())
            self.assertFalse((frame_dir / "slide_999_final.mp4").exists())
            self.assertEqual(
                extract_narrations_from_tex(str(tex_path)),
                {
                    str(page): text
                    for page, text in enumerate(self.annotations, 1)
                },
            )

    def test_missing_annotation_is_rejected(self) -> None:
        self._assert_invalid(
            self.annotations[:-1],
            "Narration cardinality mismatch",
        )

    def test_extra_annotation_is_rejected(self) -> None:
        self._assert_invalid(
            self.annotations + ("Extra narration",),
            "Narration cardinality mismatch",
        )

    def test_empty_annotation_is_rejected(self) -> None:
        annotations = self.annotations[:2] + ("",) + self.annotations[3:]
        self._assert_invalid(
            annotations,
            r"Empty narration annotations at page positions: \[3\]",
        )


if __name__ == "__main__":
    unittest.main()
