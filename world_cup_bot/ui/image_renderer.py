from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from world_cup_bot.services.prediction_view_service import (
    BracketRenderModel,
    GroupSheetRenderModel,
    RenderStatus,
)


BACKGROUND = "#171b22"
PANEL = "#202631"
PANEL_LIGHT = "#263040"
TEXT = "#edf2f7"
MUTED = "#aab4c0"
GRID = "#343c49"
CORRECT = "#3fbf7f"
INCORRECT = "#e05d5d"
PENDING = "#8d98a8"
ACCENT = "#5b8def"
THIRD_PLACE = "#d5a640"
FLAG_DIR = Path(__file__).resolve().parents[2] / "assets" / "flags"


def render_groups_png(model: GroupSheetRenderModel) -> bytes:
    width = 1800
    section_width = 420
    section_height = 250
    margin = 48
    gap = 22
    header_height = 150
    columns = 4
    rows = max(1, (len(model.groups) + columns - 1) // columns)
    height = header_height + rows * section_height + (rows - 1) * gap + margin
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    fonts = _fonts()

    _draw_header(draw, model.title, model.subtitle, model.meta, width, fonts)
    for index, group in enumerate(model.groups):
        column = index % columns
        row = index // columns
        x = margin + column * (section_width + gap)
        y = header_height + row * (section_height + gap)
        _rounded_rect(draw, (x, y, x + section_width, y + section_height), PANEL)
        draw.text((x + 20, y + 18), group.label, fill=TEXT, font=fonts["heading"])
        line_y = y + 62
        for item in group.rows:
            row_box = (x + 14, line_y - 6, x + section_width - 14, line_y + 34)
            badge = _group_advancement_badge(item)
            if badge is not None:
                label, color = badge
                draw.rounded_rectangle(
                    row_box,
                    radius=6,
                    fill="#232c3b",
                    outline=color,
                    width=1,
                )
                draw.rounded_rectangle(
                    (row_box[0], row_box[1], row_box[0] + 5, row_box[3]),
                    radius=2,
                    fill=color,
                )
            draw.text((x + 20, line_y), f"{item.position}.", fill=MUTED, font=fonts["body"])
            _draw_team(
                image,
                draw,
                x + 58,
                line_y,
                _fit(item.team_name, 14 if badge else 17),
                item.flag_code,
                fonts["body"],
            )
            if badge is not None:
                _pill(
                    draw,
                    x + 300,
                    line_y - 3,
                    label,
                    color,
                    fonts["small"],
                    width=92,
                )
            elif item.status.state != "pending":
                _pill(
                    draw,
                    x + 344,
                    line_y - 3,
                    _status_icon(item.status),
                    _status_color(item.status),
                    fonts["small"],
                )
            line_y += 42

    return _png_bytes(image)


def render_bracket_png(model: BracketRenderModel) -> bytes:
    width = 1900
    margin = 48
    header_height = 150
    column_width = 190
    gap = 14
    match_height = 60
    match_pitch = 80
    rounds = _rounds(model)
    side_slots = max(1, (len(rounds.get("Round of 32", ())) + 1) // 2)
    base_height = header_height + max(8, side_slots) * match_pitch + margin
    height = base_height
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    fonts = _fonts()

    _draw_header(draw, model.title, model.subtitle, model.meta, width, fonts)
    left_columns = {
        "Round of 32": margin,
        "Round of 16": margin + (column_width + gap),
        "Quarter-finals": margin + 2 * (column_width + gap),
        "Semi-finals": margin + 3 * (column_width + gap),
    }
    final_x = margin + 4 * (column_width + gap)
    right_columns = {
        "Semi-finals": margin + 5 * (column_width + gap),
        "Quarter-finals": margin + 6 * (column_width + gap),
        "Round of 16": margin + 7 * (column_width + gap),
        "Round of 32": margin + 8 * (column_width + gap),
    }
    side_round_labels = ("Round of 32", "Round of 16", "Quarter-finals", "Semi-finals")

    left = _side_bracket_layout(
        rounds=rounds,
        columns=left_columns,
        start_y=header_height,
        match_height=match_height,
        match_pitch=match_pitch,
        side="left",
    )
    right = _side_bracket_layout(
        rounds=rounds,
        columns=right_columns,
        start_y=header_height,
        match_height=match_height,
        match_pitch=match_pitch,
        side="right",
    )

    final = _single_match(rounds.get("Final", ()))
    third_place = _single_match(rounds.get("Third-place match", ()))
    left_semis = left.get("Semi-finals", ())
    right_semis = right.get("Semi-finals", ())
    final_y = _center_stage_y(left_semis, right_semis, header_height, match_height)
    third_y = final_y + 94
    needed_height = (
        third_y + match_height + margin
        if third_place
        else final_y + match_height + margin
    )
    if needed_height > height:
        height = needed_height
        image = Image.new("RGB", (width, height), BACKGROUND)
        draw = ImageDraw.Draw(image)
        _draw_header(draw, model.title, model.subtitle, model.meta, width, fonts)

    for label, x in left_columns.items():
        draw.text((x, header_height - 40), label, fill=ACCENT, font=fonts["small_heading"])
    draw.text((final_x, header_height - 40), "Final", fill=ACCENT, font=fonts["small_heading"])
    for label, x in right_columns.items():
        draw.text((x, header_height - 40), label, fill=ACCENT, font=fonts["small_heading"])

    for round_index in range(len(side_round_labels) - 1):
        current_label = side_round_labels[round_index]
        next_label = side_round_labels[round_index + 1]
        _draw_bracket_connectors(
            draw,
            left.get(current_label, ()),
            left.get(next_label, ()),
            column_width=column_width,
            direction="right",
        )
        _draw_bracket_connectors(
            draw,
            right.get(current_label, ()),
            right.get(next_label, ()),
            column_width=column_width,
            direction="left",
        )
    if final is not None:
        final_box = _PlacedMatch(final, final_x, final_y)
        _draw_final_connectors(
            draw,
            left_semis,
            right_semis,
            final_box,
            column_width=column_width,
        )
    if third_place is not None:
        third_box = _PlacedMatch(third_place, final_x, third_y)
        _draw_third_place_connector(
            draw,
            left_semis,
            right_semis,
            third_box,
            column_width=column_width,
        )
        draw.text(
            (final_x, third_y - 28),
            "Third-place match",
            fill=ACCENT,
            font=fonts["small_heading"],
        )

    for placements in (left, right):
        for placed_matches in placements.values():
            for placed in placed_matches:
                _draw_bracket_match(
                    image,
                    draw,
                    placed.match,
                    placed.x,
                    placed.y,
                    column_width,
                    match_height,
                    fonts,
                )
    if final is not None:
        _draw_bracket_match(
            image,
            draw,
            final,
            final_x,
            final_y,
            column_width,
            match_height,
            fonts,
        )
    if third_place is not None:
        _draw_bracket_match(
            image,
            draw,
            third_place,
            final_x,
            third_y,
            column_width,
            match_height,
            fonts,
        )

    return _png_bytes(image)


@dataclass(frozen=True)
class _PlacedMatch:
    match: object
    x: int
    y: int


def _rounds(model: BracketRenderModel) -> dict[str, list[object]]:
    rounds: dict[str, list[object]] = {}
    for match in model.matches:
        rounds.setdefault(match.round_label, []).append(match)
    return rounds


def _side_bracket_layout(
    *,
    rounds: dict[str, list[object]],
    columns: dict[str, int],
    start_y: int,
    match_height: int,
    match_pitch: int,
    side: str,
) -> dict[str, tuple[_PlacedMatch, ...]]:
    layout: dict[str, tuple[_PlacedMatch, ...]] = {}
    centers: dict[str, tuple[int, ...]] = {}
    labels = ("Round of 32", "Round of 16", "Quarter-finals", "Semi-finals")
    for label_index, label in enumerate(labels):
        matches = _side_matches(rounds.get(label, ()), side)
        if label_index == 0:
            round_centers = tuple(
                start_y + index * match_pitch + match_height // 2
                for index in range(len(matches))
            )
        else:
            previous_centers = centers.get(labels[label_index - 1], ())
            round_centers = _parent_centers(previous_centers, len(matches), match_pitch)
        centers[label] = round_centers
        layout[label] = tuple(
            _PlacedMatch(match, columns[label], round_centers[index] - match_height // 2)
            for index, match in enumerate(matches)
            if index < len(round_centers)
        )
    return layout


def _side_matches(matches: list[object] | tuple[object, ...], side: str) -> tuple[object, ...]:
    if len(matches) <= 1:
        return tuple(matches)
    midpoint = (len(matches) + 1) // 2
    if side == "left":
        return tuple(matches[:midpoint])
    return tuple(matches[midpoint:])


def _parent_centers(
    previous_centers: tuple[int, ...],
    match_count: int,
    match_pitch: int,
) -> tuple[int, ...]:
    if not previous_centers:
        return ()
    if match_count and len(previous_centers) >= match_count * 2:
        return tuple(
            (previous_centers[index] + previous_centers[index + 1]) // 2
            for index in range(0, match_count * 2, 2)
        )
    return tuple(
        previous_centers[0] + index * match_pitch
        for index in range(match_count)
    )


def _single_match(matches: list[object] | tuple[object, ...]) -> object | None:
    return matches[0] if matches else None


def _center_stage_y(
    left_semis: tuple[_PlacedMatch, ...],
    right_semis: tuple[_PlacedMatch, ...],
    fallback_y: int,
    match_height: int,
) -> int:
    centers = [
        placed.y + match_height // 2
        for placed in (*left_semis, *right_semis)
    ]
    if not centers:
        return fallback_y + match_height * 2
    return sum(centers) // len(centers) - match_height // 2


def _draw_bracket_connectors(
    draw: ImageDraw.ImageDraw,
    children: tuple[_PlacedMatch, ...],
    parents: tuple[_PlacedMatch, ...],
    *,
    column_width: int,
    direction: str,
) -> None:
    for index, parent in enumerate(parents):
        pair = children[index * 2 : index * 2 + 2]
        if not pair:
            continue
        if direction == "right":
            child_edge = pair[0].x + column_width
            parent_edge = parent.x
        else:
            child_edge = pair[0].x
            parent_edge = parent.x + column_width
        junction_x = (child_edge + parent_edge) // 2
        parent_y = _match_center(parent)
        child_ys = [_match_center(child) for child in pair]
        for child_y in child_ys:
            draw.line((child_edge, child_y, junction_x, child_y), fill=GRID, width=2)
        if len(child_ys) > 1:
            draw.line(
                (junction_x, min(child_ys), junction_x, max(child_ys)),
                fill=GRID,
                width=2,
            )
        draw.line((junction_x, parent_y, parent_edge, parent_y), fill=GRID, width=2)


def _draw_final_connectors(
    draw: ImageDraw.ImageDraw,
    left_semis: tuple[_PlacedMatch, ...],
    right_semis: tuple[_PlacedMatch, ...],
    final: _PlacedMatch,
    *,
    column_width: int,
) -> None:
    final_y = _match_center(final)
    for placed in left_semis:
        start_x = placed.x + column_width
        start_y = _match_center(placed)
        junction_x = (start_x + final.x) // 2
        draw.line((start_x, start_y, junction_x, start_y), fill=GRID, width=2)
        draw.line((junction_x, start_y, junction_x, final_y), fill=GRID, width=2)
        draw.line((junction_x, final_y, final.x, final_y), fill=GRID, width=2)
    for placed in right_semis:
        start_x = placed.x
        start_y = _match_center(placed)
        final_edge = final.x + column_width
        junction_x = (start_x + final_edge) // 2
        draw.line((start_x, start_y, junction_x, start_y), fill=GRID, width=2)
        draw.line((junction_x, start_y, junction_x, final_y), fill=GRID, width=2)
        draw.line((junction_x, final_y, final_edge, final_y), fill=GRID, width=2)


def _draw_third_place_connector(
    draw: ImageDraw.ImageDraw,
    left_semis: tuple[_PlacedMatch, ...],
    right_semis: tuple[_PlacedMatch, ...],
    third_place: _PlacedMatch,
    *,
    column_width: int,
) -> None:
    if not left_semis and not right_semis:
        return
    center_x = third_place.x + column_width // 2
    top_y = max(_match_center(third_place) - 34, 0)
    draw.line((center_x, top_y, center_x, _match_center(third_place)), fill=GRID, width=2)


def _match_center(placed: _PlacedMatch) -> int:
    return placed.y + 30


def _draw_bracket_match(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    match: object,
    x: int,
    y: int,
    width: int,
    height: int,
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    status = getattr(match, "status")
    outline = _status_color(status)
    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=7,
        fill=PANEL,
        outline=outline,
        width=1,
    )
    home_is_winner = _is_match_winner(
        match,
        getattr(match, "home_team_name"),
        getattr(match, "home_flag_code"),
    )
    away_is_winner = _is_match_winner(
        match,
        getattr(match, "away_team_name"),
        getattr(match, "away_flag_code"),
    )
    _draw_bracket_team_row(
        image,
        draw,
        x,
        y + 4,
        width,
        getattr(match, "home_team_name"),
        getattr(match, "home_flag_code"),
        home_is_winner,
        status,
        fonts,
    )
    _draw_bracket_team_row(
        image,
        draw,
        x,
        y + 31,
        width,
        getattr(match, "away_team_name"),
        getattr(match, "away_flag_code"),
        away_is_winner,
        status,
        fonts,
    )


def _draw_bracket_team_row(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    name: str,
    flag_code: str | None,
    is_winner: bool,
    status: RenderStatus,
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    if is_winner:
        draw.rounded_rectangle(
            (x + 5, y - 1, x + width - 5, y + 24),
            radius=5,
            fill=PANEL_LIGHT,
        )
        draw.rectangle((x + 5, y - 1, x + 9, y + 24), fill=_status_color(status))
    _draw_team(
        image,
        draw,
        x + 13,
        y + 2,
        _fit(name, 9),
        flag_code,
        fonts["small"],
        fill=TEXT if is_winner else MUTED,
    )
    if is_winner:
        _pill(
            draw,
            x + width - 43,
            y + 1,
            _status_icon(status),
            _status_color(status),
            fonts["tiny"],
            width=34,
            height=22,
            radius=6,
        )


def _is_match_winner(
    match: object,
    team_name: str,
    flag_code: str | None,
) -> bool:
    return (
        getattr(match, "winner_team_name") == team_name
        and getattr(match, "winner_flag_code") == flag_code
    )


def _group_advancement_badge(row: object) -> tuple[str, str] | None:
    if row.position <= 2:
        if row.status.state == "pending":
            return ("ADV", ACCENT)
        return (f"ADV {_status_icon(row.status)}", _status_color(row.status))
    if row.position == 3 and row.third_place_status is not None:
        if row.third_place_status.state == "pending":
            return ("3P PICK", THIRD_PLACE)
        return (
            f"3P {_status_icon(row.third_place_status)}",
            _status_color(row.third_place_status),
        )
    return None


def _draw_header(
    draw: ImageDraw.ImageDraw,
    title: str,
    subtitle: str,
    meta: tuple[str, ...],
    width: int,
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    draw.text((48, 34), title, fill=TEXT, font=fonts["title"])
    draw.text((48, 88), subtitle, fill=MUTED, font=fonts["body"])
    meta_text = "  |  ".join(meta)
    draw.text((width - 48, 92), meta_text, fill=MUTED, font=fonts["small"], anchor="ra")
    draw.line((48, 132, width - 48, 132), fill=GRID, width=2)


def _pill(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    label: str,
    color: str,
    font: ImageFont.ImageFont,
    *,
    width: int = 48,
    height: int = 28,
    radius: int = 8,
) -> None:
    box = (x, y, x + width, y + height)
    draw.rounded_rectangle(box, radius=radius, fill=color)
    draw.text((x + width // 2, y + height // 2), label, fill="#ffffff", font=font, anchor="mm")


def _draw_team(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    name: str,
    flag_code: str | None,
    font: ImageFont.ImageFont,
    *,
    fill: str = TEXT,
) -> None:
    flag = _flag_image(flag_code, width=28, height=20)
    text_x = x
    if flag is not None:
        image.paste(flag, (x, y + 3), flag)
        text_x += 36
    draw.text((text_x, y), name, fill=fill, font=font)


def _draw_team_pair(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    home_name: str,
    home_flag_code: str | None,
    away_name: str,
    away_flag_code: str | None,
    font: ImageFont.ImageFont,
) -> None:
    _draw_team(
        image,
        draw,
        x,
        y,
        _fit(home_name, 9),
        home_flag_code,
        font,
        fill=MUTED,
    )
    draw.text((x + 116, y), "vs", fill=MUTED, font=font)
    _draw_team(
        image,
        draw,
        x + 146,
        y,
        _fit(away_name, 9),
        away_flag_code,
        font,
        fill=MUTED,
    )


def _rounded_rect(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    fill: str,
) -> None:
    draw.rounded_rectangle(box, radius=8, fill=fill, outline=GRID, width=1)


def _status_color(status: RenderStatus) -> str:
    if status.state == "correct":
        return CORRECT
    if status.state == "incorrect":
        return INCORRECT
    return PENDING


def _status_icon(status: RenderStatus) -> str:
    if status.state == "correct":
        return "✓"
    if status.state == "incorrect":
        return "✕"
    return "..."


@lru_cache(maxsize=96)
def _flag_image(
    flag_code: str | None,
    *,
    width: int,
    height: int,
) -> Image.Image | None:
    if not flag_code:
        return None
    path = FLAG_DIR / f"{flag_code.upper()}.svg"
    if not path.exists():
        return None
    try:
        import cairosvg
    except Exception:
        return None

    try:
        png = cairosvg.svg2png(
            url=str(path),
            output_width=width,
            output_height=height,
        )
    except Exception:
        return None
    if not png:
        return None
    return Image.open(BytesIO(png)).convert("RGBA")


def _fonts() -> dict[str, ImageFont.ImageFont]:
    return {
        "title": _font(42),
        "heading": _font(28),
        "small_heading": _font(24),
        "body": _font(24),
        "small": _font(18),
        "tiny": _font(15),
    }


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
