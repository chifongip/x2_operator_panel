"""Local static-map loading and browser raster conversion."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import cos, isfinite, sin
from pathlib import Path
import struct
from typing import Any
import zlib

import yaml


_MAX_MAP_PIXELS = 100_000_000


@dataclass(frozen=True)
class MapAsset:
    """A Nav2 map and the browser-safe PNG rendered from its PGM source."""

    width: int
    height: int
    resolution: float
    origin_x: float
    origin_y: float
    origin_yaw: float
    png: bytes
    version: str

    def metadata(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "resolution": self.resolution,
            "origin": {
                "x": self.origin_x,
                "y": self.origin_y,
                "yaw": self.origin_yaw,
            },
            "version": self.version,
            "image_url": "/api/map/image",
        }

    def map_to_pixel(self, x: float, y: float) -> tuple[float, float]:
        """Transform map-frame meters to top-left-origin image pixels."""
        dx = x - self.origin_x
        dy = y - self.origin_y
        map_x = cos(self.origin_yaw) * dx + sin(self.origin_yaw) * dy
        map_y = -sin(self.origin_yaw) * dx + cos(self.origin_yaw) * dy
        return (
            map_x / self.resolution,
            self.height - map_y / self.resolution,
        )


def load_map_asset(yaml_path: str | Path) -> MapAsset:
    """Load a standard trinary Nav2 map and encode its PGM as a grayscale PNG."""
    map_yaml = Path(yaml_path).resolve()
    if not map_yaml.is_file():
        raise ValueError(f"Map YAML does not exist: {map_yaml}")

    try:
        document = yaml.safe_load(map_yaml.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid map YAML: {error}") from error
    if not isinstance(document, dict):
        raise ValueError("Map YAML must contain a mapping")

    image_name = document.get("image")
    if not isinstance(image_name, str) or not image_name:
        raise ValueError("Map YAML requires a non-empty image field")
    image_path = (map_yaml.parent / image_name).resolve()
    if map_yaml.parent not in image_path.parents or not image_path.is_file():
        raise ValueError("Map image must be a file under the map YAML directory")

    resolution = _finite_number(document.get("resolution"), "resolution")
    if resolution <= 0.0:
        raise ValueError("Map resolution must be positive")
    origin = document.get("origin")
    if not isinstance(origin, list) or len(origin) != 3:
        raise ValueError("Map origin must contain x, y, and yaw")
    origin_x = _finite_number(origin[0], "origin x")
    origin_y = _finite_number(origin[1], "origin y")
    origin_yaw = _finite_number(origin[2], "origin yaw")

    pgm = image_path.read_bytes()
    width, height, pixels = _read_pgm(pgm)
    version = sha256(map_yaml.read_bytes() + pgm).hexdigest()[:16]
    return MapAsset(
        width=width,
        height=height,
        resolution=resolution,
        origin_x=origin_x,
        origin_y=origin_y,
        origin_yaw=origin_yaw,
        png=_encode_grayscale_png(width, height, pixels),
        version=version,
    )


def _finite_number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"Map {name} must be numeric")
    converted = float(value)
    if not isfinite(converted):
        raise ValueError(f"Map {name} must be finite")
    return converted


def _read_pgm(payload: bytes) -> tuple[int, int, bytes]:
    """Read the raw 8-bit P5 variant emitted by Nav2 map generation."""
    position = 0

    def token() -> bytes:
        nonlocal position
        while position < len(payload):
            if payload[position] in b" \t\r\n":
                position += 1
                continue
            if payload[position] == ord("#"):
                while position < len(payload) and payload[position] not in b"\r\n":
                    position += 1
                continue
            break
        begin = position
        while position < len(payload) and payload[position] not in b" \t\r\n":
            position += 1
        if begin == position:
            raise ValueError("Malformed PGM header")
        return payload[begin:position]

    if token() != b"P5":
        raise ValueError("Map image must be an 8-bit binary PGM (P5)")
    try:
        width = int(token())
        height = int(token())
        maximum = int(token())
    except ValueError as error:
        raise ValueError("Malformed PGM dimensions") from error
    if width <= 0 or height <= 0 or maximum != 255:
        raise ValueError("Map PGM must have positive dimensions and max value 255")
    if width * height > _MAX_MAP_PIXELS:
        raise ValueError("Map PGM exceeds the supported pixel limit")
    if position >= len(payload) or payload[position] not in b" \t\r\n":
        raise ValueError("Malformed PGM raster separator")
    if payload[position:position + 2] == b"\r\n":
        position += 2
    else:
        position += 1
    pixels = payload[position:]
    if len(pixels) != width * height:
        raise ValueError("PGM raster length does not match its dimensions")
    return width, height, pixels


def _encode_grayscale_png(width: int, height: int, pixels: bytes) -> bytes:
    rows = b"".join(
        b"\x00" + pixels[row * width:(row + 1) * width]
        for row in range(height)
    )

    def chunk(name: bytes, contents: bytes) -> bytes:
        return (
            struct.pack(">I", len(contents))
            + name
            + contents
            + struct.pack(">I", zlib.crc32(name + contents) & 0xFFFFFFFF)
        )

    return b"\x89PNG\r\n\x1a\n" + b"".join(
        (
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(rows, level=9)),
            chunk(b"IEND", b""),
        )
    )
