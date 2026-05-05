from __future__ import annotations

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
TEXT = "#edf2f7"
MUTED = "#aab4c0"
GRID = "#343c49"
CORRECT = "#3fbf7f"
INCORRECT = "#e05d5d"
PENDING = "#8d98a8"
ACCENT = "#5b8def"
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
            status_color = _status_color(item.status)
            draw.text((x + 20, line_y), f"{item.position}.", fill=MUTED, font=fonts["body"])
            _draw_team(
                image,
                draw,
                x + 58,
                line_y,
                _fit(item.team_name, 15),
                item.flag_code,
                fonts["body"],
            )
            _pill(draw, x + 292, line_y - 3, item.status.label, status_color, fonts["small"])
            if item.third_place_status is not None:
                _pill(
                    draw,
                    x + 348,
                    line_y - 3,
                    item.third_place_status.label,
                    _status_color(item.third_place_status),
                    fonts["small"],
                )
            line_y += 42

    return _png_bytes(image)


def render_bracket_png(model: BracketRenderModel) -> bytes:
    width = 1900
    margin = 48
    header_height = 150
    column_width = 292
    gap = 20
    rounds = _rounds(model)
    max_matches = max((len(matches) for matches in rounds.values()), default=1)
    row_height = 78
    height = header_height + max_matches * row_height + margin
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    fonts = _fonts()

    _draw_header(draw, model.title, model.subtitle, model.meta, width, fonts)
    for column, (round_label, matches) in enumerate(rounds.items()):
        x = margin + column * (column_width + gap)
        draw.text((x, header_height - 40), round_label, fill=ACCENT, font=fonts["small_heading"])
        for row, match in enumerate(matches):
            y = header_height + row * row_height
            _rounded_rect(draw, (x, y, x + column_width, y + row_height - 12), PANEL)
            _draw_team_pair(
                image,
                draw,
                x + 14,
                y + 12,
                match.home_team_name,
                match.home_flag_code,
                match.away_team_name,
                match.away_flag_code,
                fonts["small"],
            )
            _draw_team(
                image,
                draw,
                x + 14,
                y + 38,
                _fit(match.winner_team_name, 14),
                match.winner_flag_code,
                fonts["body"],
            )
            _pill(
                draw,
                x + column_width - 58,
                y + 34,
                match.status.label,
                _status_color(match.status),
                fonts["small"],
            )

    return _png_bytes(image)


def _rounds(model: BracketRenderModel) -> dict[str, list[object]]:
    rounds: dict[str, list[object]] = {}
    for match in model.matches:
        rounds.setdefault(match.round_label, []).append(match)
    return rounds


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
) -> None:
    box = (x, y, x + 48, y + 28)
    draw.rounded_rectangle(box, radius=8, fill=color)
    draw.text((x + 24, y + 14), label, fill="#ffffff", font=font, anchor="mm")


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
