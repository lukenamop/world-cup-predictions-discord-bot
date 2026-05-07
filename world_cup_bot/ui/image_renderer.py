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


def render_groups_png(model: GroupSheetRenderModel) -> bytes:
    margin = 48
    gap = 22
    header_height = 150
    columns = 4
    fonts = _fonts()
    section_width = _group_section_width(model, fonts)
    section_height = 250
    width = max(1800, margin + columns * section_width + (columns - 1) * gap + 6)
    rows = max(1, (len(model.groups) + columns - 1) // columns)
    height = header_height + rows * section_height + (rows - 1) * gap + margin
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)

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
                item.team_name,
                item.flag_code,
                fonts["body"],
            )
            if badge is not None:
                _pill(
                    draw,
                    x + section_width - 120,
                    line_y - 3,
                    label,
                    color,
                    fonts["small"],
                    width=92,
                )
            elif item.status.state != "pending":
                _pill(
                    draw,
                    x + section_width - 76,
                    line_y - 3,
                    _status_icon(item.status),
                    _status_color(item.status),
                    fonts["small"],
                )
            line_y += 42

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
    column_width = _bracket_column_width(rounds, fonts)
    left_columns, final_x, right_columns = _bracket_columns(
        margin=margin,
        column_width=column_width,
        gap=gap,
    )
    width = max(1900, right_columns["Round of 32"] + column_width + margin)
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

    for label, x in left_columns.items():
        _draw_round_label(draw, x, round_label_y, column_width, label, fonts)
    _draw_round_label(draw, final_x, round_label_y, column_width, "Final", fonts)
    for label, x in right_columns.items():
        _draw_round_label(draw, x, round_label_y, column_width, label, fonts)

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
        _draw_champion_callout(
            image,
            draw,
            final,
            third_place,
            model.champion_status,
            model.runner_up_status,
            model.third_place_status,
            final_x,
            final_y - 196,
            column_width,
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

    return _png_bytes(image)


def _bracket_columns(
    *,
    margin: int,
    column_width: int,
    gap: int,
) -> tuple[dict[str, int], int, dict[str, int]]:
    normal_step = column_width + gap
    compact_step = column_width - min(BRACKET_COMPACT_OVERLAP, column_width // 5)
    left_columns = {
        "Round of 32": margin,
        "Round of 16": margin + normal_step,
    }
    left_columns["Quarter-finals"] = left_columns["Round of 16"] + compact_step
    left_columns["Semi-finals"] = left_columns["Quarter-finals"] + compact_step
    final_x = left_columns["Semi-finals"] + normal_step
    right_columns = {"Semi-finals": final_x + normal_step}
    right_columns["Quarter-finals"] = right_columns["Semi-finals"] + compact_step
    right_columns["Round of 16"] = right_columns["Quarter-finals"] + compact_step
    right_columns["Round of 32"] = right_columns["Round of 16"] + normal_step
    return left_columns, final_x, right_columns


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
    return max(420, 58 + 36 + max_name_width + 12 + 120 + 14)


def _bracket_column_width(
    rounds: dict[str, list[object]],
    fonts: dict[str, ImageFont.ImageFont],
) -> int:
    max_name_width = max(
        (
            _text_width(name, fonts["small"])
            for matches in rounds.values()
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
    draw.line((x - 5, y - 5, x + 5, y + 5), fill="#ffffff", width=2)
    draw.line((x + 5, y - 5, x - 5, y + 5), fill="#ffffff", width=2)


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
