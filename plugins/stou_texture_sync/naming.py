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
    try:
        return re.compile("^" + "".join(out) + r"\.(?P<ext>[A-Za-z0-9]+)$")
    except re.error as exc:
        raise NamingError(f"invalid preset template {template!r}: {exc}") from exc


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
        validate_basename(self.texture_set)
        for udim, part in self.tile_parts.items():
            if type(udim) is not int or not 1001 <= udim <= 1999:
                raise NamingError(f"{self.texture_set}: invalid UDIM {udim!r}")
            if not _valid_part(part):
                raise NamingError(f"{self.texture_set}: invalid name part {part!r}")
        if self.character_prefix and not _valid_part(self.character_prefix):
            raise NamingError(f"{self.texture_set}: invalid name part {self.character_prefix!r}")
        if self.mode == "body_part":
            if not self.tile_parts:
                raise NamingError(f"{self.texture_set}: body-part mode needs a Tile mapping")
            parts = list(self.tile_parts.values())
            if len({part.casefold() for part in parts}) != len(parts):
                raise NamingError(f"{self.texture_set}: two Tiles map to the same body part")


def _valid_part(part: str) -> bool:
    return isinstance(part, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-]*", part))


def validate_basename(name: str) -> None:
    if (not isinstance(name, str) or not name or name.endswith((" ", "."))
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3]",
                            name.split(".", 1)[0], re.IGNORECASE)):
        raise NamingError(f"unsafe file name {name!r}")


def final_name(file_name: str, templates: list[str], naming: TextureSetNaming) -> str:
    """Return the final source-image name for one exported file."""
    validate_basename(file_name)
    naming.validate()
    matches = []
    for template in templates:
        match = template_regex(template, naming.texture_set).fullmatch(file_name)
        if match:
            matches.append((template, match))
    if not matches:
        raise NamingError(f"{file_name}: does not match any preset template")
    if len(matches) > 1:
        raise NamingError(f"{file_name}: matches several preset templates")
    template, match = matches[0]
    color = match.group("colorSpace") if "colorSpace" in match.re.groupindex else None
    udim = match.group("udim") if "udim" in match.re.groupindex else None
    if udim and not 1001 <= int(udim) <= 1999:
        raise NamingError(f"{file_name}: invalid UDIM {udim}")
    ext = match.group("ext")
    kind = map_type(template)
    color_suffix = f"_{color}" if color else ""
    if naming.mode == "keep":
        if udim and not naming.strip_tile_suffix:
            return file_name
        name = f"T_{naming.texture_set}_{kind}{color_suffix}.{ext}"
        validate_basename(name)
        return name
    if not udim:
        raise NamingError(f"{file_name}: body-part mode needs a Tile suffix")
    part = naming.tile_parts.get(int(udim))
    if part is None:
        raise NamingError(f"{file_name}: Tile {udim} has no body-part mapping")
    prefix = f"{naming.character_prefix}_" if naming.character_prefix else ""
    name = f"T_{prefix}{part}_{kind}{color_suffix}.{ext}"
    validate_basename(name)
    return name


_COLOR_SPACE_RE = re.compile(r"[a-z0-9\-]*[a-z][a-z0-9\-]*")
_COLOR_CHARS_RE = re.compile(r"[a-z0-9\-]*")


@dataclass(frozen=True)
class FinalNamePattern:
    """The set of final file stems (no extension, case-folded) one template can produce.

    ``suffix is None`` is the exact stem ``prefix``. Otherwise the stem is
    ``prefix + C + suffix`` where ``C`` is any legal ``$colorSpace`` value.
    """

    prefix: str
    suffix: str | None = None
    label: str = field(default="", compare=False)

    def describe(self) -> str:
        return self.label or (self.prefix if self.suffix is None
                              else f"{self.prefix}<colorSpace>{self.suffix}")


def _parse_template(template: str) -> list:
    """Nested list of literal characters, ('tok', name) and ('group', children)."""
    stack: list[list] = [[]]
    i = 0
    while i < len(template):
        char = template[i]
        if char == "(":
            stack.append([])
        elif char == ")":
            children = stack.pop()
            stack[-1].append(("group", children))
        elif char == "$":
            name = re.match(r"\$([A-Za-z]+)", template[i:]).group(1)
            stack[-1].append(("tok", name))
            i += len(name) + 1
            continue
        else:
            stack[-1].append(char)
        i += 1
    return stack[0]


def _has_token(nodes: list, name: str) -> bool:
    return any(node == ("tok", name) or (isinstance(node, tuple) and node[0] == "group"
                                         and _has_token(node[1], name)) for node in nodes)


def _variants(nodes: list, with_udim: bool) -> list[list]:
    """Every flat token sequence an optional-group choice can produce."""
    variants: list[list] = [[]]
    for node in nodes:
        if isinstance(node, tuple) and node[0] == "group":
            included = _variants(node[1], with_udim)
            if _has_token(node[1], "udim"):
                choices = included if with_udim else [[]]
            else:
                choices = included + [[]]
        else:
            choices = [[node]]
        variants = [variant + choice for variant in variants for choice in choices]
    return variants


def final_name_patterns(template: str, naming: TextureSetNaming,
                        udim: int | None) -> list[FinalNamePattern]:
    """All final-stem patterns ``template`` can produce for one Texture Set and Tile."""
    template_regex(template, naming.texture_set)
    naming.validate()
    nodes = _parse_template(template)
    if udim is None and any(node == ("tok", "udim") for node in nodes):
        raise NamingError(f"{naming.texture_set}: the preset requires a Tile suffix")
    part = None
    if naming.mode == "body_part":
        if udim is None:
            raise NamingError(f"{naming.texture_set}: body-part mode needs a Tile suffix")
        part = naming.tile_parts.get(udim)
        if part is None:
            raise NamingError(f"{naming.texture_set}: Tile {udim} has no body-part mapping")
    kind = map_type(template)
    patterns: list[FinalNamePattern] = []
    for variant in _variants(nodes, udim is not None):
        before, after, hole, has_udim = [], [], False, False
        for node in variant:
            if node == ("tok", "colorSpace"):
                hole = True
                continue
            if node == ("tok", "textureSet"):
                text = naming.texture_set
            elif node == ("tok", "udim"):
                text, has_udim = str(udim), True
            else:
                text = node
            (after if hole else before).append(text)
        if naming.mode == "keep" and has_udim and not naming.strip_tile_suffix:
            prefix, suffix = "".join(before), "".join(after)
        else:
            if naming.mode == "body_part":
                if not has_udim:
                    raise NamingError(f"{naming.texture_set}: body-part mode needs a Tile suffix")
                head = f"T_{naming.character_prefix + '_' if naming.character_prefix else ''}{part}_{kind}"
            else:
                head = f"T_{naming.texture_set}_{kind}"
            prefix, suffix = (head + "_", "") if hole else (head, "")
        validate_basename(f"{prefix}{'c' + suffix if hole else ''}.png")
        pattern = FinalNamePattern(
            prefix.casefold(), suffix.casefold() if hole else None,
            f"{prefix}{'<colorSpace>' + suffix if hole else ''}.png")
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns


def _legal_chars(text: str) -> bool:
    return _COLOR_CHARS_RE.fullmatch(text) is not None


def _legal_color(text: str) -> bool:
    return _COLOR_SPACE_RE.fullmatch(text) is not None


def _holes_overlap(p1: str, s1: str, p2: str, s2: str) -> bool:
    """Whether p1+C1+s1 == p2+C2+s2 for some legal colour spaces C1 and C2."""
    if len(p1) > len(p2):
        p1, s1, p2, s2 = p2, s2, p1, s1
    if not p2.startswith(p1):
        return False
    dp = p2[len(p1):]                      # C1 + s1 == dp + C2 + s2
    if len(s2) > len(s1):
        if not s2.endswith(s1):
            return False
        return _legal_chars(dp) and _legal_chars(s2[:len(s2) - len(s1)])  # C1 == dp+C2+ds
    if not s1.endswith(s2):
        return False
    ds = s1[:len(s1) - len(s2)]            # C1 + ds == dp + C2
    if _legal_chars(dp) and _legal_chars(ds):
        return True
    for k in range(1, len(dp)):            # dp == C1 + t and ds == t + C2
        if (ds.startswith(dp[len(dp) - k:]) and _legal_color(dp[:len(dp) - k])
                and _legal_color(ds[k:])):
            return True
    return False


def patterns_overlap(a: FinalNamePattern, b: FinalNamePattern) -> bool:
    """Whether two patterns can produce the same final stem."""
    if a.suffix is None and b.suffix is None:
        return a.prefix == b.prefix
    if a.suffix is None or b.suffix is None:
        literal, hole = (a, b) if a.suffix is None else (b, a)
        text = literal.prefix
        if (len(text) < len(hole.prefix) + len(hole.suffix) or not text.startswith(hole.prefix)
                or not text.endswith(hole.suffix)):
            return False
        return _legal_color(text[len(hole.prefix):len(text) - len(hole.suffix)])
    return _holes_overlap(a.prefix, a.suffix, b.prefix, b.suffix)


def find_pattern_overlaps(items) -> list[tuple]:
    """Return ``(owner_a, pattern_a, owner_b, pattern_b)`` for every colliding pair.

    ``items`` is an iterable of ``(owner, pattern)``; pairs of one owner never collide.
    """
    literals: dict[str, list] = {}
    holes: dict[str, list] = {}
    for owner, pattern in items:
        bucket = literals if pattern.suffix is None else holes
        bucket.setdefault(pattern.prefix, []).append((owner, pattern))
    found = []

    def add(first, second):
        if first[0] != second[0] and patterns_overlap(first[1], second[1]):
            found.append((*first, *second))

    for prefix, group in literals.items():
        for index, first in enumerate(group):
            for second in group[index + 1:]:
                add(first, second)
        for end in range(len(prefix) + 1):
            for hole in holes.get(prefix[:end], ()):
                for literal in group:
                    add(literal, hole)
    for prefix, group in holes.items():
        for index, first in enumerate(group):
            for second in group[index + 1:]:
                add(first, second)
        for end in range(len(prefix)):
            for shorter in holes.get(prefix[:end], ()):
                for hole in group:
                    add(shorter, hole)
    return found


def plan_names(files: dict[str, list[str]], templates: list[str],
               namings: dict[str, TextureSetNaming]) -> dict[str, str]:
    """Map exported paths (grouped by Texture Set) to unique final names.

    Raises NamingError listing every problem, so nothing is published from an
    ambiguous configuration.
    """
    result, errors, owners, seen = {}, [], {}, set()
    for texture_set, paths in files.items():
        naming = namings.get(texture_set)
        if naming is None:
            errors.append(f"{texture_set}: no naming configuration")
            continue
        for path in paths:
            basename = re.split(r"[/\\]", os.fspath(path))[-1]
            path_key = os.path.normpath(os.fspath(path).replace("\\", "/")).casefold()
            if path_key in seen:
                errors.append(f"{basename}: exported more than once")
                continue
            seen.add(path_key)
            try:
                name = final_name(basename, templates, naming)
            except NamingError as exc:
                errors.append(str(exc))
                continue
            key = name.casefold()
            if key in owners:
                errors.append(f"{name}: produced by both {owners[key]} and {basename}")
                continue
            owners[key] = basename
            result[path] = name
    if errors:
        raise NamingError("; ".join(errors))
    return result
