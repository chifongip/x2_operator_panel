from pathlib import Path
from math import pi
import unittest

from x2_operator_panel.map_model import MapAsset, load_map_asset
from x2_operator_panel.ros_gateway import load_navigation_presets


def write_map(tmp_path: Path) -> Path:
    image_path = tmp_path / "map.pgm"
    image_path.write_bytes(
        b"P5\n# small test map\n4 3\n255\n"
        b"\x00\x7f\xff\x00\xff\x00\x7f\xff\x00\xff\x00\x7f"
    )
    map_path = tmp_path / "map.yaml"
    map_path.write_text(
        "image: map.pgm\nresolution: 0.5\norigin: [-1.0, -2.0, 0.0]\n",
        encoding="utf-8",
    )
    return map_path


class MapModelTest(unittest.TestCase):
    def test_load_map_asset_encodes_browser_png_and_transforms_coordinates(self):
        with self.subTest("map asset"):
            with self._temporary_directory() as tmp_path:
                asset = load_map_asset(write_map(Path(tmp_path)))

                self.assertEqual(asset.width, 4)
                self.assertEqual(asset.height, 3)
                self.assertTrue(asset.png.startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertEqual(asset.map_to_pixel(-1.0, -2.0), (0.0, 3.0))
                self.assertEqual(asset.map_to_pixel(1.0, -0.5), (4.0, 0.0))
                self.assertEqual(asset.metadata()["image_url"], "/api/map/image")

    def test_map_rejects_non_p5_images(self):
        with self._temporary_directory() as tmp_path:
            tmp_dir = Path(tmp_path)
            map_path = write_map(tmp_dir)
            (tmp_dir / "map.pgm").write_bytes(b"P2\n1 1\n255\n0\n")

            with self.assertRaisesRegex(ValueError, "P5"):
                load_map_asset(map_path)

    def test_map_accepts_crlf_header_separator(self):
        with self._temporary_directory() as tmp_path:
            tmp_dir = Path(tmp_path)
            map_path = write_map(tmp_dir)
            (tmp_dir / "map.pgm").write_bytes(b"P5\r\n2 1\r\n255\r\n\x00\xff")

            asset = load_map_asset(map_path)

            self.assertEqual((asset.width, asset.height), (2, 1))

    def test_map_coordinate_conversion_respects_origin_yaw(self):
        asset = MapAsset(
            width=20,
            height=20,
            resolution=1.0,
            origin_x=10.0,
            origin_y=5.0,
            origin_yaw=pi / 2.0,
            png=b"",
            version="test",
        )

        self.assertAlmostEqual(asset.map_to_pixel(10.0, 6.0)[0], 1.0)
        self.assertAlmostEqual(asset.map_to_pixel(9.0, 5.0)[1], 19.0)

    def test_navigation_presets_require_unique_finite_poses(self):
        with self._temporary_directory() as tmp_path:
            preset_path = Path(tmp_path) / "presets.yaml"
            preset_path.write_text(
                "presets:\n"
                "  - id: loading_bay\n"
                "    label: Loading bay\n"
                "    pose: {x: 1.0, y: -2.0, yaw: 1.57}\n",
                encoding="utf-8",
            )

            presets = load_navigation_presets(preset_path)

            self.assertEqual(
                [preset.as_dict() for preset in presets],
                [
                    {
                        "id": "loading_bay",
                        "label": "Loading bay",
                        "pose": {"x": 1.0, "y": -2.0, "yaw": 1.57},
                    }
                ],
            )

    def _temporary_directory(self):
        import tempfile

        return tempfile.TemporaryDirectory()
