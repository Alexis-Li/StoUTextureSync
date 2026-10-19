"""Map Painter output file names to final source-image names.

Templates come from the export preset (for example
``T_$textureSet_Albedo(_$colorSpace)(_$udim)``). The Texture Set name is taken
from the binding, never guessed from underscores in the file name.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


class NamingError(ValueError):
    """A configuration or output that cannot be mapped to one unique final name."""


_TOKENS = {
    "textureSet": None,  # replaced by the escaped bound Texture Set name
    # A colour-space name contains a letter, so a bare Tile number is never taken for one.
    "colorSpace": r"(?P<colorSpace>[A-Za-z0-9\-]*[A-Za-z][A-Za-z0-9\-]*)",
    "udim": r"(?P<udim>1\d{3})",
}


def template_regex(template: str, texture_set: str) -> re.Pattern:
    """Compile a preset file-name template for one bound Texture Set."""
    out, i, depth = [], 0, 0
    while i < len(template):
        char = template[i]
        if char == "(":
            out.append("(?:")
            depth += 1
        elif char == ")":
            if depth == 0:
                raise NamingError(f"unbalanced ')' in template {template!r}")
            out.append(")?")
            depth -= 1
        elif char == "$":
            match = re.match(r"\$([A-Za-z]+)", template[i:])
            if not match or match.group(1) not in _TOKENS:
                raise NamingError(f"unsupported token in template {template!r} at {i}")
            name = match.group(1)
            out.append(f"(?P<textureSet>{re.escape(texture_set)})" if name == "textureSet"
                       else _TOKENS[name])
            i += len(match.group(0))
            continue
        else:
            out.append(re.escape(char))
        i += 1
    if depth:
        raise NamingError(f"unbalanced '(' in template {template!r}")
    return re.compile("^" + "".join(out) + r"\.(?P<ext>[A-Za-z0-9]+)$")


def map_type(template: str) -> str:
    """The literal map-type part of a template: T_$textureSet_Albedo(...) -> Albedo."""
    core = re.sub(r"\([^()]*\)", "", template)
    core = core.replace("$textureSet", "\0")
    tail = core.split("\0", 1)[-1].strip("_")
    if not tail or "$" in tail:
        raise NamingError(f"template has no literal map type: {template!r}")
    return tail


def udim_to_uv(udim: int) -> tuple[int, int]:
    index = udim - 1001
    return index % 10, index // 10


def uv_to_udim(u: int, v: int) -> int:
    return 1001 + u + 10 * v


@dataclass
class TextureSetNaming:
    """Naming configuration of one bound Texture Set."""

    texture_set: str
    mode: str = "keep"                     # "keep" or "body_part"
    strip_tile_suffix: bool = True
    character_prefix: str = ""
    tile_parts: dict[int, str] = field(default_factory=dict)  # UDIM -> body part

    def validate(self) -> None:
        if self.mode not in ("keep", "body_part"):
            raise NamingError(f"unknown naming mode {self.mode!r}")
        if self.mode == "body_part":
            if not self.tile_parts:
                raise NamingError(f"{self.texture_set}: body-part mode needs a Tile mapping")
            parts = list(self.tile_parts.values())
            if len(set(parts)) != len(parts):
                raise NamingError(f"{self.texture_set}: two Tiles map to the same body part")
            for part in parts + [self.character_prefix]:
                if part and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-]*", part):
                    raise NamingError(f"{self.texture_set}: invalid name part {part!r}")


def final_name(file_name: str, templates: list[str], naming: TextureSetNaming) -> str:
    """Return the final source-image name for one exported file."""
    naming.validate()
    matches = []
    for template in templates:
        match = template_regex(template, naming.texture_set).match(file_name)
        if match:
            matches.append((template, match))
    if not matches:
        raise NamingError(f"{file_name}: does not match any preset template")
    if len(matches) > 1:
        raise NamingError(f"{file_name}: matches several preset templates")
    template, match = matches[0]
    color = match.group("colorSpace") if "colorSpace" in match.re.groupindex else None
    udim = match.group("udim") if "udim" in match.re.groupindex else None
    ext = match.group("ext")
    kind = map_type(template)
    color_suffix = f"_{color}" if color else ""
    if naming.mode == "keep":
        if udim and not naming.strip_tile_suffix:
            return file_name
        return f"T_{naming.texture_set}_{kind}{color_suffix}.{ext}"
    if not udim:
        raise NamingError(f"{file_name}: body-part mode needs a Tile suffix")
    part = naming.tile_parts.get(int(udim))
    if part is None:
        raise NamingError(f"{file_name}: Tile {udim} has no body-part mapping")
    prefix = f"{naming.character_prefix}_" if naming.character_prefix else ""
    return f"T_{prefix}{part}_{kind}{color_suffix}.{ext}"


def plan_names(files: dict[str, list[str]], templates: list[str],
               namings: dict[str, TextureSetNaming]) -> dict[str, str]:
    """Map exported paths (grouped by Texture Set) to unique final names.

    Raises NamingError listing every problem, so nothing is published from an
    ambiguous configuration.
    """
    result, errors, owners = {}, [], {}
    for texture_set, paths in files.items():
        naming = namings.get(texture_set)
        if naming is None:
            errors.append(f"{texture_set}: no naming configuration")
            continue
        for path in paths:
            try:
                name = final_name(os.path.basename(path), templates, naming)
            except NamingError as exc:
                errors.append(str(exc))
                continue
            key = name.lower()
            if key in owners:
                errors.append(f"{name}: produced by both {owners[key]} and {os.path.basename(path)}")
                continue
            owners[key] = os.path.basename(path)
            result[path] = name
    if errors:
        raise NamingError("; ".join(errors))
    return result
