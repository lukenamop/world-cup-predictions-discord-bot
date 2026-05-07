from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from world_cup_bot.services.prediction_view_service import (
    BracketRenderModel,
    GroupSheetRenderModel,
    RenderStatus,
)


BACKGROUND = "#171b22"
PANEL = "#202631"
TEXT = "#edf2f7"
MUTED = "#aab4c0"
GRID = "#343c49"
CORRECT = "#3fbf7f"
INCORRECT = "#e05d5d"
PENDING = "#8d98a8"
ACCENT = "#5b8def"
THIRD_PLACE = "#d5a640"
FLAG_DIR = Path(__file__).resolve().parents[2] / "assets" / "flags"
TROPHY_PATH = Path(__file__).resolve().parents[2] / "assets" / "trophy" / "world-cup-trophy.png"
FONT_PATH = (
    Path(__file__).resolve().parents[2]
    / "assets"
    / "fonts"
    / "AtkinsonHyperlegible-Regular.ttf"
)
BRACKET_COMPACT_OVERLAP = 46
BRACKET_BADGE_WIDTH = 34
BRACKET_BADGE_GAP = 10
GROUP_ROW_HEIGHT = 40
GROUP_FULL_BADGE_WIDTH = 92
GROUP_ICON_BADGE_WIDTH = 48
GROUP_BADGE_RIGHT_PADDING = 28
GROUP_BADGE_GAP = 14


def render_groups_png(model: GroupSheetRenderModel) -> bytes:
    margin = 48
    gap = 22
    header_height = 176
    header_divider_y = 158
    columns = 4
    fonts = _fonts()
    section_width = _group_section_width(model, fonts)
    section_height = 250
    width = max(1800, margin + columns * section_width + (columns - 1) * gap + 6)
    rows = max(1, (len(model.groups) + columns - 1) // columns)
    height = header_height + rows * section_height + (rows - 1) * gap + margin
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)

    _draw_header(
        draw,
        model.title,
        model.subtitle,
        model.meta,
        width,
        fonts,
        divider_y=header_divider_y,
    )
    _draw_group_legend(draw, width, fonts)
    for index, group in enumerate(model.groups):
        column = index % columns
        row = index // columns
        x = margin + column * (section_width + gap)
        y = header_height + row * (section_height + gap)
        _rounded_rect(draw, (x, y, x + section_width, y + section_height), PANEL)
        draw.text((x + 20, y + 18), group.label, fill=TEXT, font=fonts["heading"])
        first_row_y = y + 56
        cutoff_position = _group_cutoff_position(group)
        for row_index, item in enumerate(group.rows):
            row_y = first_row_y + row_index * 42
            if row_index + 1 > cutoff_position:
                row_y += 12
            _draw_group_row(
                image,
                draw,
                item,
                x,
                row_y,
                section_width,
                fonts,
                is_below_cutoff=row_index + 1 > cutoff_position,
            )
        _draw_group_cutoff(draw, cutoff_position, x, first_row_y, section_width)

    return _png_bytes(image)


def render_bracket_png(model: BracketRenderModel) -> bytes:
    margin = 48
    header_height = 176
    header_divider_y = 158
    round_label_y = 126
    gap = 18
    match_height = 60
    match_pitch = 80
    rounds = _rounds(model)
    fonts = _fonts()
    side_round_labels = ("Round of 32", "Round of 16", "Quarter-finals", "Semi-finals")
    left_widths = _side_column_widths(rounds, fonts, side="left")
    right_widths = _side_column_widths(rounds, fonts, side="right")
    final_column = _BracketColumn(
        x=0,
        width=_bracket_column_width(
            (
                *rounds.get("Final", ()),
                *rounds.get("Third-place match", ()),
            ),
            fonts,
        ),
    )
    left_columns, final_column, right_columns = _bracket_columns(
        margin=margin,
        left_widths=left_widths,
        final_column=final_column,
        right_widths=right_widths,
        gap=gap,
    )
    width = max(
        1900,
        right_columns["Round of 32"].x
        + right_columns["Round of 32"].width
        + margin,
    )
    side_slots = max(1, (len(rounds.get("Round of 32", ())) + 1) // 2)
    base_height = header_height + max(8, side_slots) * match_pitch + margin
    height = base_height
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)

    _draw_header(
        draw,
        model.title,
        model.subtitle,
        model.meta,
        width,
        fonts,
        divider_y=header_divider_y,
    )
    _draw_bracket_legend(draw, width, fonts)

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
    needed_height = final_y + match_height + margin
    if needed_height > height:
        height = needed_height
        image = Image.new("RGB", (width, height), BACKGROUND)
        draw = ImageDraw.Draw(image)
        _draw_header(
            draw,
            model.title,
            model.subtitle,
            model.meta,
            width,
            fonts,
            divider_y=header_divider_y,
        )

    for label, column in left_columns.items():
        _draw_round_label(draw, column.x, round_label_y, column.width, label, fonts)
    _draw_round_label(
        draw,
        final_column.x,
        round_label_y,
        final_column.width,
        "Final",
        fonts,
    )
    for label, column in right_columns.items():
        _draw_round_label(draw, column.x, round_label_y, column.width, label, fonts)

    for round_index in range(len(side_round_labels) - 1):
        current_label = side_round_labels[round_index]
        next_label = side_round_labels[round_index + 1]
        _draw_bracket_connectors(
            draw,
            left.get(current_label, ()),
            left.get(next_label, ()),
            direction="right",
        )
        _draw_bracket_connectors(
            draw,
            right.get(current_label, ()),
            right.get(next_label, ()),
            direction="left",
        )
    if final is not None:
        final_box = _PlacedMatch(final, final_column.x, final_y, final_column.width)
        _draw_final_connectors(
            draw,
            left_semis,
            right_semis,
            final_box,
        )
        _draw_champion_callout(
            image,
            draw,
            final,
            third_place,
            model.champion_status,
            model.runner_up_status,
            model.third_place_status,
            final_column.x,
            final_y - 196,
            final_column.width,
            fonts,
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
                    placed.width,
                    match_height,
                    fonts,
                )
    if final is not None:
        _draw_bracket_match(
            image,
            draw,
            final,
            final_column.x,
            final_y,
            final_column.width,
            match_height,
            fonts,
        )

    return _png_bytes(image)


def _bracket_columns(
    *,
    margin: int,
    left_widths: dict[str, int],
    final_column: "_BracketColumn",
    right_widths: dict[str, int],
    gap: int,
) -> tuple[dict[str, "_BracketColumn"], "_BracketColumn", dict[str, "_BracketColumn"]]:
    left_columns = {
        "Round of 32": _BracketColumn(margin, left_widths["Round of 32"]),
    }
    left_columns["Round of 16"] = _next_column(
        left_columns["Round of 32"],
        width=left_widths["Round of 16"],
        gap=gap,
    )
    left_columns["Quarter-finals"] = _next_column(
        left_columns["Round of 16"],
        width=left_widths["Quarter-finals"],
        gap=-_compact_overlap(left_columns["Round of 16"].width),
    )
    left_columns["Semi-finals"] = _next_column(
        left_columns["Quarter-finals"],
        width=left_widths["Semi-finals"],
        gap=-_compact_overlap(left_columns["Quarter-finals"].width),
    )
    final_column = _BracketColumn(
        x=left_columns["Semi-finals"].x + left_columns["Semi-finals"].width + gap,
        width=final_column.width,
    )
    right_columns = {
        "Semi-finals": _next_column(
            final_column,
            width=right_widths["Semi-finals"],
            gap=gap,
        ),
    }
    right_columns["Quarter-finals"] = _next_column(
        right_columns["Semi-finals"],
        width=right_widths["Quarter-finals"],
        gap=-_compact_overlap(right_columns["Semi-finals"].width),
    )
    right_columns["Round of 16"] = _next_column(
        right_columns["Quarter-finals"],
        width=right_widths["Round of 16"],
        gap=-_compact_overlap(right_columns["Quarter-finals"].width),
    )
    right_columns["Round of 32"] = _next_column(
        right_columns["Round of 16"],
        width=right_widths["Round of 32"],
        gap=gap,
    )
    return left_columns, final_column, right_columns


def _next_column(previous: "_BracketColumn", *, width: int, gap: int) -> "_BracketColumn":
    return _BracketColumn(
        x=previous.x + previous.width + gap,
        width=width,
    )


def _compact_overlap(width: int) -> int:
    return min(BRACKET_COMPACT_OVERLAP, width // 5)


def _draw_round_label(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    column_width: int,
    label: str,
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    draw.text(
        (x + column_width // 2, y),
        label,
        fill=ACCENT,
        font=fonts["small_heading"],
        anchor="ma",
    )


@dataclass(frozen=True)
class _BracketColumn:
    x: int
    width: int


@dataclass(frozen=True)
class _PlacedMatch:
    match: object
    x: int
    y: int
    width: int


def _rounds(model: BracketRenderModel) -> dict[str, list[object]]:
    rounds: dict[str, list[object]] = {}
    for match in model.matches:
        rounds.setdefault(match.round_label, []).append(match)
    return rounds


def _side_bracket_layout(
    *,
    rounds: dict[str, list[object]],
    columns: dict[str, _BracketColumn],
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
            _PlacedMatch(
                match,
                columns[label].x,
                round_centers[index] - match_height // 2,
                columns[label].width,
            )
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
    direction: str,
) -> None:
    for index, parent in enumerate(parents):
        pair = children[index * 2 : index * 2 + 2]
        if not pair:
            continue
        if direction == "right":
            child_edge = pair[0].x + pair[0].width
            parent_edge = parent.x
        else:
            child_edge = pair[0].x
            parent_edge = parent.x + parent.width
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
) -> None:
    final_y = _match_center(final)
    for placed in left_semis:
        start_x = placed.x + placed.width
        start_y = _match_center(placed)
        junction_x = (start_x + final.x) // 2
        draw.line((start_x, start_y, junction_x, start_y), fill=GRID, width=2)
        draw.line((junction_x, start_y, junction_x, final_y), fill=GRID, width=2)
        draw.line((junction_x, final_y, final.x, final_y), fill=GRID, width=2)
    for placed in right_semis:
        start_x = placed.x
        start_y = _match_center(placed)
        final_edge = final.x + final.width
        junction_x = (start_x + final_edge) // 2
        draw.line((start_x, start_y, junction_x, start_y), fill=GRID, width=2)
        draw.line((junction_x, start_y, junction_x, final_y), fill=GRID, width=2)
        draw.line((junction_x, final_y, final_edge, final_y), fill=GRID, width=2)


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
    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=7,
        fill=PANEL,
        outline=GRID,
        width=1,
    )
    _draw_bracket_team_row(
        image,
        draw,
        x,
        y + 4,
        width,
        getattr(match, "home_team_name"),
        getattr(match, "home_flag_code"),
        getattr(match, "home_status"),
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
        getattr(match, "away_status"),
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
    status: RenderStatus,
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    team_x = x + 13
    badge_x = _bracket_badge_x(x, width)
    _draw_team(
        image,
        draw,
        team_x,
        y + 2,
        name,
        flag_code,
        fonts["small"],
        fill=TEXT if status.state == "correct" else MUTED,
        max_width=badge_x - team_x - BRACKET_BADGE_GAP,
    )
    _bracket_status_badge(
        draw,
        badge_x,
        y + 1,
        status,
        fonts["tiny"],
    )


def _draw_champion_callout(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    final: object,
    third_place: object | None,
    champion_status: RenderStatus,
    runner_up_status: RenderStatus,
    third_place_status: RenderStatus | None,
    x: int,
    y: int,
    width: int,
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    callout_width = max(width + 116, 384)
    x -= (callout_width - width) // 2
    height = 154 if third_place is not None else 124
    box = (x, y, x + callout_width, y + height)
    draw.rounded_rectangle(
        box,
        radius=8,
        fill="#1d2430",
        outline=THIRD_PLACE,
        width=1,
    )
    _draw_trophy_image(image, x + 18, y + 12, height=78)
    label_x = x + 80
    team_x = x + 152
    champion_badge_x = _bracket_badge_x(x, callout_width)
    draw.text((label_x, y + 16), "Champion", fill=THIRD_PLACE, font=fonts["small"])
    _draw_team(
        image,
        draw,
        label_x,
        y + 44,
        getattr(final, "winner_team_name"),
        getattr(final, "winner_flag_code"),
        fonts["body"],
        max_width=champion_badge_x - label_x - BRACKET_BADGE_GAP,
    )
    _bracket_status_badge(
        draw,
        champion_badge_x,
        y + 47,
        champion_status,
        fonts["tiny"],
    )
    draw.line((x + 16, y + 82, x + callout_width - 16, y + 82), fill=GRID, width=1)
    runner_up_name, runner_up_flag = _match_loser(final)
    _draw_placement_row(
        image,
        draw,
        x + 18,
        y + 96,
        "2",
        "Runner-up",
        runner_up_name,
        runner_up_flag,
        runner_up_status,
        callout_width,
        team_x=team_x,
        fonts=fonts,
    )
    if third_place is not None:
        _draw_placement_row(
            image,
            draw,
            x + 18,
            y + 124,
            "3",
            "Third",
            getattr(third_place, "winner_team_name"),
            getattr(third_place, "winner_flag_code"),
            third_place_status or RenderStatus(label="...", state="pending"),
            callout_width,
            team_x=team_x,
            fonts=fonts,
        )


def _draw_placement_row(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    rank: str,
    label: str,
    team_name: str,
    flag_code: str | None,
    status: RenderStatus,
    width: int,
    team_x: int,
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    draw.ellipse((x, y + 1, x + 20, y + 21), fill=GRID)
    draw.text((x + 10, y + 11), rank, fill=TEXT, font=fonts["tiny"], anchor="mm")
    draw.text((x + 30, y + 2), label, fill=MUTED, font=fonts["small"])
    card_x = x - 18
    badge_x = _bracket_badge_x(card_x, width)
    _draw_team(
        image,
        draw,
        team_x,
        y,
        team_name,
        flag_code,
        fonts["small"],
        max_width=badge_x - team_x - BRACKET_BADGE_GAP,
    )
    _bracket_status_badge(draw, badge_x, y + 1, status, fonts["tiny"])


def _bracket_badge_x(x: int, width: int) -> int:
    return x + width - 13 - BRACKET_BADGE_WIDTH


def _match_loser(match: object) -> tuple[str, str | None]:
    home_name = getattr(match, "home_team_name")
    home_flag = getattr(match, "home_flag_code")
    away_name = getattr(match, "away_team_name")
    away_flag = getattr(match, "away_flag_code")
    winner_name = getattr(match, "winner_team_name")
    winner_flag = getattr(match, "winner_flag_code")
    if home_name == winner_name and home_flag == winner_flag:
        return away_name, away_flag
    return home_name, home_flag


def _draw_group_legend(
    draw: ImageDraw.ImageDraw,
    width: int,
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    x = width - 400
    y = 36
    text_y = y + 14
    draw.text((x, text_y), "Status", fill=MUTED, font=fonts["small"], anchor="lm")
    x += 68
    for label, color, text in (
        ("+3", CORRECT, "earned"),
        ("✕", INCORRECT, "missed"),
    ):
        _pill(
            draw,
            x,
            y,
            label,
            color,
            fonts["small"],
            width=GROUP_ICON_BADGE_WIDTH,
        )
        draw.text(
            (x + GROUP_ICON_BADGE_WIDTH + 10, text_y),
            text,
            fill=MUTED,
            font=fonts["small"],
            anchor="lm",
        )
        x += 138


def _draw_bracket_legend(
    draw: ImageDraw.ImageDraw,
    width: int,
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    x = width - 430
    y = 36
    items = (
        (RenderStatus(label="+5", state="correct"), "earned"),
        (RenderStatus(label="X", state="incorrect"), "missed"),
    )
    text_y = y + 11
    draw.text((x, text_y), "Scoring", fill=MUTED, font=fonts["small"], anchor="lm")
    x += 76
    for status, label in items:
        _bracket_status_badge(draw, x, y, status, fonts["tiny"])
        draw.text((x + 42, text_y), label, fill=MUTED, font=fonts["small"], anchor="lm")
        x += 132


def _bracket_status_badge(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    status: RenderStatus,
    font: ImageFont.ImageFont,
    *,
    width: int = 34,
    height: int = 22,
) -> None:
    if status.state == "pending":
        return
    color = _status_color(status)
    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=6,
        fill=color,
    )
    if status.state == "correct":
        label = status.label if status.label.startswith("+") else "+"
        draw.text((x + width // 2, y + height // 2), label, fill="#ffffff", font=font, anchor="mm")
        return
    if status.state == "incorrect":
        _draw_x_icon(draw, x + width // 2, y + height // 2)
        return


def _draw_x_icon(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    size = 6
    draw.line((x - size, y - size, x + size, y + size), fill="#ffffff", width=3)
    draw.line((x + size, y - size, x - size, y + size), fill="#ffffff", width=3)


def _draw_trophy_image(image: Image.Image, x: int, y: int, *, height: int) -> None:
    trophy = _trophy_image(height=height)
    if trophy is None:
        return
    center_x = x + trophy.width // 2
    center_y = y + trophy.height // 2
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        (
            center_x - 34,
            center_y - 42,
            center_x + 34,
            center_y + 42,
        ),
        fill=(213, 166, 64, 82),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(16))
    image.paste(glow, (0, 0), glow)
    image.paste(trophy, (x, y), trophy)


@lru_cache(maxsize=8)
def _trophy_image(*, height: int) -> Image.Image | None:
    if not TROPHY_PATH.exists():
        return None
    try:
        source = Image.open(TROPHY_PATH).convert("RGBA")
    except Exception:
        return None
    width = max(1, round(source.width * height / source.height))
    return source.resize((width, height), Image.Resampling.LANCZOS)


def _draw_group_row(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    row: object,
    x: int,
    y: int,
    section_width: int,
    fonts: dict[str, ImageFont.ImageFont],
    *,
    is_below_cutoff: bool,
) -> None:
    center_y = y + GROUP_ROW_HEIGHT // 2
    marker = None if is_below_cutoff else _group_advancement_marker(row)
    row_fill = MUTED
    if marker is not None and marker[2] == "correct":
        row_fill = TEXT

    team_x = x + 58
    full_badge_x = _group_badge_x(x, section_width, GROUP_FULL_BADGE_WIDTH)
    draw.text(
        (x + 20, center_y),
        f"{row.position}.",
        fill=MUTED,
        font=fonts["body"],
        anchor="lm",
    )
    _draw_group_team(
        image,
        draw,
        team_x,
        center_y,
        getattr(row, "team_name"),
        getattr(row, "flag_code"),
        fonts["body"],
        max_width=full_badge_x - team_x - GROUP_BADGE_GAP,
        fill=row_fill,
    )
    if marker is not None:
        label, color, state = marker
        if state == "pending":
            return
        _pill(
            draw,
            full_badge_x,
            center_y - 14,
            label,
            color,
            fonts["small"],
            width=GROUP_FULL_BADGE_WIDTH,
        )


def _draw_group_team(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    x: int,
    center_y: int,
    name: str,
    flag_code: str | None,
    font: ImageFont.ImageFont,
    *,
    max_width: int,
    fill: str,
) -> None:
    flag = _flag_image(flag_code, width=28, height=20)
    text_x = x
    if flag is not None:
        image.paste(flag, (x, center_y - 10), flag)
        text_x += 36
    text = _fit_to_width(name, font, max_width - (text_x - x))
    draw.text((text_x, center_y), text, fill=fill, font=font, anchor="lm")


def _group_badge_x(x: int, section_width: int, badge_width: int) -> int:
    return x + section_width - GROUP_BADGE_RIGHT_PADDING - badge_width


def _draw_group_cutoff(
    draw: ImageDraw.ImageDraw,
    cutoff_position: int,
    x: int,
    first_row_y: int,
    section_width: int,
) -> None:
    y = first_row_y + cutoff_position * 42 + 4
    start_x = x + 22
    end_x = x + section_width - 22
    dash = 10
    gap = 7
    cursor = start_x
    while cursor < end_x:
        draw.line((cursor, y, min(cursor + dash, end_x), y), fill="#6f7b8b", width=1)
        cursor += dash + gap


def _group_cutoff_position(group: object) -> int:
    return 3 if _group_has_advancing_third(group) else 2


def _group_has_advancing_third(group: object) -> bool:
    return any(
        getattr(row, "position") == 3 and getattr(row, "third_place_status") is not None
        for row in getattr(group, "rows")
    )


def _group_advancement_marker(row: object) -> tuple[str, str, str] | None:
    if row.position <= 2:
        if row.status.state == "pending":
            return ("", ACCENT, "pending")
        label = (
            row.status.label
            if row.status.state == "correct"
            else _status_icon(row.status)
        )
        return (
            label,
            _status_color(row.status),
            row.status.state,
        )
    if row.position == 3 and row.third_place_status is not None:
        if row.third_place_status.state == "pending":
            return ("", THIRD_PLACE, "pending")
        label = (
            row.third_place_status.label
            if row.third_place_status.state == "correct"
            else _status_icon(row.third_place_status)
        )
        return (
            label,
            _status_color(row.third_place_status),
            row.third_place_status.state,
        )
    return None


def _group_section_width(
    model: GroupSheetRenderModel,
    fonts: dict[str, ImageFont.ImageFont],
) -> int:
    max_name_width = max(
        (
            _text_width(row.team_name, fonts["body"])
            for group in model.groups
            for row in group.rows
        ),
        default=0,
    )
    return max(
        420,
        58
        + 36
        + max_name_width
        + GROUP_BADGE_GAP
        + GROUP_FULL_BADGE_WIDTH
        + GROUP_BADGE_RIGHT_PADDING
        + 14,
    )


def _side_column_widths(
    rounds: dict[str, list[object]],
    fonts: dict[str, ImageFont.ImageFont],
    *,
    side: str,
) -> dict[str, int]:
    return {
        label: _bracket_column_width(_side_matches(rounds.get(label, ()), side), fonts)
        for label in ("Round of 32", "Round of 16", "Quarter-finals", "Semi-finals")
    }


def _bracket_column_width(
    matches: list[object] | tuple[object, ...],
    fonts: dict[str, ImageFont.ImageFont],
) -> int:
    max_name_width = max(
        (
            _text_width(name, fonts["small"])
            for match in matches
            for name in (
                getattr(match, "home_team_name"),
                getattr(match, "away_team_name"),
            )
        ),
        default=0,
    )
    return max(230, max_name_width + 108)


def _text_width(value: str, font: ImageFont.ImageFont) -> int:
    left, _, right, _ = font.getbbox(value)
    return right - left


def _draw_header(
    draw: ImageDraw.ImageDraw,
    title: str,
    subtitle: str,
    meta: tuple[str, ...],
    width: int,
    fonts: dict[str, ImageFont.ImageFont],
    *,
    divider_y: int = 132,
) -> None:
    draw.text((48, 34), title, fill=TEXT, font=fonts["title"])
    draw.text((48, 88), subtitle, fill=MUTED, font=fonts["body"])
    meta_text = "  |  ".join(meta)
    draw.text((width - 48, 92), meta_text, fill=MUTED, font=fonts["small"], anchor="ra")
    draw.line((48, divider_y, width - 48, divider_y), fill=GRID, width=2)


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
    icon = None
    if "✓" in label:
        icon = "check"
        label = label.replace("✓", "").strip()
    elif "✕" in label:
        icon = "x"
        label = label.replace("✕", "").strip()
    if icon is None:
        draw.text((x + width // 2, y + height // 2), label, fill="#ffffff", font=font, anchor="mm")
        return

    if label:
        draw.text(
            (x + (width - 22) // 2, y + height // 2),
            label,
            fill="#ffffff",
            font=font,
            anchor="mm",
        )
        icon_x = x + width - 18
    else:
        icon_x = x + width // 2
    _draw_status_icon(draw, icon, icon_x, y + height // 2)


def _draw_status_icon(
    draw: ImageDraw.ImageDraw,
    icon: str,
    x: int,
    y: int,
) -> None:
    if icon == "check":
        draw.line((x - 6, y, x - 2, y + 5, x + 7, y - 6), fill="#ffffff", width=2)
        return
    _draw_x_icon(draw, x, y)


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
    max_width: int | None = None,
) -> None:
    flag = _flag_image(flag_code, width=28, height=20)
    text_x = x
    text = name
    if flag is not None:
        image.paste(flag, (x, y + 3), flag)
        text_x += 36
    if max_width is not None:
        text = _fit_to_width(text, font, max_width - (text_x - x))
    draw.text((text_x, y), text, fill=fill, font=font)


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
    for path, index in (
        (FONT_PATH, 0),
        ("/System/Library/Fonts/Avenir Next.ttc", 5),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
        ("/System/Library/Fonts/Supplemental/Helvetica.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ):
        try:
            return ImageFont.truetype(path, size=size, index=index)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def _fit_to_width(value: str, font: ImageFont.ImageFont, max_width: int) -> str:
    if _text_width(value, font) <= max_width:
        return value
    ellipsis = "..."
    if max_width <= _text_width(ellipsis, font):
        return ""
    for length in range(len(value), 0, -1):
        candidate = value[:length].rstrip() + ellipsis
        if _text_width(candidate, font) <= max_width:
            return candidate
    return ""


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
