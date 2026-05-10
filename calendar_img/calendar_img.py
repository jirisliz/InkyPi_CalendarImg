import hashlib
import json
import logging
import os
from datetime import datetime, date, timedelta

import requests
from icalendar import Calendar
import recurring_ical_events
from PIL import Image, ImageDraw, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin

logger = logging.getLogger(__name__)

# Full day/month names
DAY_FULL_CS   = ["", "Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek", "Sobota", "Neděle"]
DAY_FULL_EN   = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTH_FULL_CS = ["", "Leden", "Únor", "Březen", "Duben", "Květen", "Červen",
                 "Červenec", "Srpen", "Září", "Říjen", "Listopad", "Prosinec"]
MONTH_FULL_EN = ["", "January", "February", "March", "April", "May", "June",
                 "July", "August", "September", "October", "November", "December"]
DAY_SHORT_CS  = ["", "Po", "Út", "St", "Čt", "Pá", "So", "Ne"]
DAY_SHORT_EN  = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

DEFAULT_CAL_COLORS = [
    ("#FBBD02", "#FFF2AD"),
    ("#1A73E8", "#D2E8FB"),
    ("#34A853", "#D4EDDA"),
    ("#EA4335", "#FCDBD9"),
    ("#9C27B0", "#EAD5F5"),
]


def hex_to_rgb(hex_str, fallback=(0, 0, 0)):
    try:
        h = hex_str.strip().lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback


class CalendarImgPlugin(BasePlugin):
    """
    Landscape split-screen InkyPi plugin.
    Left : monthly calendar grid OR agenda list (multiple iCal feeds, per-feed colours).
    Right: image slideshow — cycles through uploaded images on each refresh.
    Split ratio is configurable as a percentage.
    """

    # ------------------------------------------------------------------ #
    #  Entry point                                                         #
    # ------------------------------------------------------------------ #

    def generate_image(self, settings, device_config):
        width, height = device_config.get_resolution()

        # Calendar width as a percentage (default 65 %)
        cal_pct       = max(10, min(90, int(settings.get("cal_pct", 65))))
        calendar_width = int(width * cal_pct / 100)
        image_width    = width - calendar_width

        # ── Calendar settings ─────────────────────────────────────────
        cal_style   = settings.get("cal_style",  "grid")
        font_size   = int(settings.get("font_size", 14))
        agenda_days = int(settings.get("agenda_days", 14))
        language    = settings.get("language", "en")

        # ── Colour settings ───────────────────────────────────────────
        cal_bg          = hex_to_rgb(settings.get("cal_bg",          "#FFFFFF"), (255, 255, 255))
        cal_text        = hex_to_rgb(settings.get("cal_text",        "#000000"), (0,   0,   0  ))
        cal_header_bg   = hex_to_rgb(settings.get("cal_header_bg",   "#000000"), (0,   0,   0  ))
        cal_header_text = hex_to_rgb(settings.get("cal_header_text", "#FFFFFF"), (255, 255, 255))
        divider_color   = hex_to_rgb(settings.get("divider_color",   "#000000"), (0,   0,   0  ))
        image_bg        = hex_to_rgb(settings.get("image_bg",        "#000000"), (0,   0,   0  ))

        colors = dict(
            cal_bg=cal_bg, cal_text=cal_text,
            cal_header_bg=cal_header_bg, cal_header_text=cal_header_text,
            divider_color=divider_color,
        )

        # ── iCal feeds ────────────────────────────────────────────────
        ical_feeds = self._parse_ical_feeds(settings)
        if not ical_feeds:
            raise RuntimeError(
                "Please add at least one iCal URL in the Calendar feeds section."
            )

        today = date.today()

        if cal_style == "agenda":
            agenda_end = today + timedelta(days=agenda_days)
            events     = self._fetch_all_agenda(ical_feeds, today, agenda_end)
        else:
            month_start = today.replace(day=1)
            next_month  = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            month_end   = next_month - timedelta(days=1)
            events      = self._fetch_all_grid(ical_feeds, month_start, month_end)

        # ── Image slideshow ───────────────────────────────────────────
        images_b64   = self._parse_slide_images(settings)
        slide_img    = self._next_slide(images_b64, image_width, height, image_bg)

        # ── Render ────────────────────────────────────────────────────
        if cal_style == "agenda":
            cal_img = self._render_agenda(
                calendar_width, height, today, events, font_size, language, colors
            )
        else:
            cal_img = self._render_calendar(
                calendar_width, height, today, month_start, month_end,
                events, font_size, language, colors
            )

        canvas = Image.new("RGB", (width, height), "white")
        canvas.paste(cal_img,  (0, 0))
        canvas.paste(slide_img, (calendar_width, 0))

        draw = ImageDraw.Draw(canvas)
        draw.line([(calendar_width, 0), (calendar_width, height)],
                  fill=divider_color, width=2)
        return canvas

    # ------------------------------------------------------------------ #
    #  iCal feed parsing                                                   #
    # ------------------------------------------------------------------ #

    def _parse_ical_feeds(self, settings):
        """
        Parse ical_feeds JSON field.
        Falls back to legacy single ical_url.
        Returns [{url, border_rgb, fill_rgb, name}, ...]
        """
        feeds     = []
        feeds_json = settings.get("ical_feeds", "").strip()

        if feeds_json:
            try:
                for i, entry in enumerate(json.loads(feeds_json)):
                    url = entry.get("url", "").strip()
                    if not url:
                        continue
                    def_bdr, def_fil = DEFAULT_CAL_COLORS[i % len(DEFAULT_CAL_COLORS)]
                    feeds.append({
                        "url":        url,
                        "border_rgb": hex_to_rgb(entry.get("border", def_bdr)),
                        "fill_rgb":   hex_to_rgb(entry.get("fill",   def_fil)),
                        "name":       entry.get("name", f"Calendar {i+1}"),
                    })
            except Exception as e:
                logger.warning(f"Could not parse ical_feeds JSON: {e}")

        # Legacy single URL fallback
        if not feeds:
            url = settings.get("ical_url", "").strip()
            if url:
                def_bdr, def_fil = DEFAULT_CAL_COLORS[0]
                feeds.append({
                    "url":        url,
                    "border_rgb": hex_to_rgb(def_bdr),
                    "fill_rgb":   hex_to_rgb(def_fil),
                    "name":       "Calendar",
                })
        return feeds

    def _fetch_ical(self, url):
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            return Calendar.from_ical(resp.text)
        except Exception as e:
            raise RuntimeError(f"Failed to fetch calendar ({url}): {e}")

    def _fetch_all_grid(self, feeds, start, end):
        """Return {date: [{"summary", "border_rgb", "fill_rgb"}, ...]}"""
        result = {}
        for feed in feeds:
            try:
                cal = self._fetch_ical(feed["url"])
                raw = recurring_ical_events.of(cal).between(
                    datetime(start.year, start.month, start.day),
                    datetime(end.year,   end.month,   end.day, 23, 59, 59),
                )
            except Exception as e:
                logger.warning(f"Grid fetch error ({feed['url']}): {e}")
                continue
            for c in raw:
                if c.name == "VEVENT":
                    dt = c.get("DTSTART").dt
                    d  = dt.date() if isinstance(dt, datetime) else dt
                    result.setdefault(d, []).append({
                        "summary":    str(c.get("SUMMARY", "")),
                        "border_rgb": feed["border_rgb"],
                        "fill_rgb":   feed["fill_rgb"],
                    })
        return result

    def _fetch_all_agenda(self, feeds, start, end):
        """Return sorted list of event dicts."""
        result = []
        for feed in feeds:
            try:
                cal = self._fetch_ical(feed["url"])
                raw = recurring_ical_events.of(cal).between(
                    datetime(start.year, start.month, start.day),
                    datetime(end.year,   end.month,   end.day, 23, 59, 59),
                )
            except Exception as e:
                logger.warning(f"Agenda fetch error ({feed['url']}): {e}")
                continue
            for c in raw:
                if c.name == "VEVENT":
                    dt        = c.get("DTSTART").dt
                    dt_end    = c.get("DTEND")
                    is_allday = not isinstance(dt, datetime)
                    d         = dt if is_allday else dt.date()
                    summary   = str(c.get("SUMMARY", ""))
                    time_str  = ""
                    if not is_allday:
                        time_str = dt.strftime("%H:%M")
                        if dt_end:
                            end_dt = dt_end.dt
                            if isinstance(end_dt, datetime):
                                time_str += "–" + end_dt.strftime("%H:%M")
                    result.append({
                        "date":       d,
                        "summary":    summary,
                        "time_str":   time_str,
                        "allday":     is_allday,
                        "border_rgb": feed["border_rgb"],
                        "fill_rgb":   feed["fill_rgb"],
                    })
        result.sort(key=lambda e: (e["date"], e["time_str"]))
        return result

    # ------------------------------------------------------------------ #
    #  Image slideshow                                                     #
    # ------------------------------------------------------------------ #

    def _parse_slide_images(self, settings):
        """
        Parse uploaded images stored as base64 data-URIs in settings.
        The JS encodes each selected file as a data:image/...;base64,... string
        and stores the list as JSON in the 'slide_images' setting key.
        Returns list of base64 data-URI strings.
        """
        images_json = settings.get("slide_images", "").strip()
        if not images_json:
            return []
        try:
            parsed = json.loads(images_json)
            return [s for s in parsed if s and s.startswith("data:image")]
        except Exception as e:
            logger.warning(f"Could not parse slide_images: {e}")
            return []

    def _next_slide(self, images_b64, width, height, bg_color):
        """
        Pick the next image from the base64 list using a persistent index.
        Returns a PIL Image of exactly (width, height).
        """
        import base64
        from io import BytesIO

        blank = Image.new("RGB", (width, height), bg_color)

        if not images_b64:
            draw = ImageDraw.Draw(blank)
            try:
                font_dir = os.path.join(os.path.dirname(__file__),
                                        "..", "base_plugin", "fonts")
                font = ImageFont.truetype(
                    os.path.join(font_dir, "DejaVuSans.ttf"), 14)
            except Exception:
                font = ImageFont.load_default()
            draw.text((width // 2, height // 2),
                      "No images uploaded", font=font,
                      fill=(180, 180, 180), anchor="mm")
            return blank

        # Persistent slide index stored next to the plugin file
        state_path = os.path.join(os.path.dirname(__file__), ".slide_index")
        try:
            with open(state_path) as f:
                idx = int(f.read().strip())
        except Exception:
            idx = 0

        idx      = idx % len(images_b64)
        next_idx = (idx + 1) % len(images_b64)

        try:
            with open(state_path, "w") as f:
                f.write(str(next_idx))
        except Exception as e:
            logger.warning(f"Could not save slide index: {e}")

        logger.info(f"[calendar_img] Slideshow: image {idx + 1}/{len(images_b64)}")

        try:
            data_uri = images_b64[idx]
            # data:image/jpeg;base64,/9j/4AA...
            header, b64data = data_uri.split(",", 1)
            raw = base64.b64decode(b64data)
            img = Image.open(BytesIO(raw)).convert("RGB")
        except Exception as e:
            logger.warning(f"Could not decode image {idx}: {e}")
            draw = ImageDraw.Draw(blank)
            draw.text((width // 2, height // 2),
                      "Image decode failed", fill=(200, 80, 80), anchor="mm")
            return blank

        # Fit preserving aspect ratio; letterbox with bg_color
        img.thumbnail((width, height), Image.LANCZOS)
        panel = Image.new("RGB", (width, height), bg_color)
        panel.paste(img, ((width - img.width) // 2, (height - img.height) // 2))
        return panel

    # ------------------------------------------------------------------ #
    #  Shared rendering helpers                                            #
    # ------------------------------------------------------------------ #

    def _load_fonts(self, base_size=14):
        font_dir = os.path.join(os.path.dirname(__file__),
                                "..", "base_plugin", "fonts")
        try:
            bold   = ImageFont.truetype(
                os.path.join(font_dir, "DejaVuSans-Bold.ttf"), base_size + 4)
            medium = ImageFont.truetype(
                os.path.join(font_dir, "DejaVuSans-Bold.ttf"), base_size)
            small  = ImageFont.truetype(
                os.path.join(font_dir, "DejaVuSans.ttf"),      base_size - 2)
            tiny   = ImageFont.truetype(
                os.path.join(font_dir, "DejaVuSans.ttf"),      base_size - 4)
        except Exception:
            bold = medium = small = tiny = ImageFont.load_default()
        return bold, medium, small, tiny

    def _draw_rounded_rect(self, draw, x0, y0, x1, y1, radius,
                           fill=None, outline=None, width=1):
        r = radius
        if fill:
            draw.rectangle([x0+r, y0,   x1-r, y1  ], fill=fill)
            draw.rectangle([x0,   y0+r, x1,   y1-r], fill=fill)
            draw.ellipse([x0,     y0,     x0+2*r, y0+2*r], fill=fill)
            draw.ellipse([x1-2*r, y0,     x1,     y0+2*r], fill=fill)
            draw.ellipse([x0,     y1-2*r, x0+2*r, y1    ], fill=fill)
            draw.ellipse([x1-2*r, y1-2*r, x1,     y1    ], fill=fill)
        if outline:
            draw.rounded_rectangle([x0, y0, x1, y1],
                                   radius=r, outline=outline, width=width)

    def _truncate(self, draw, text, font, max_w):
        if draw.textlength(text, font=font) <= max_w:
            return text
        while text and draw.textlength(text + "…", font=font) > max_w:
            text = text[:-1]
        return text.rstrip() + "…"

    # ------------------------------------------------------------------ #
    #  Grid calendar renderer                                              #
    # ------------------------------------------------------------------ #

    def _render_calendar(self, width, height, today, month_start, month_end,
                         events, font_size, language, colors):
        bg      = colors["cal_bg"]
        fg      = colors["cal_text"]
        hdr_bg  = colors["cal_header_bg"]
        hdr_fg  = colors["cal_header_text"]

        img  = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)
        bold, medium, small, tiny = self._load_fonts(font_size)

        day_short = DAY_SHORT_CS if language == "cs" else DAY_SHORT_EN
        padding   = 10
        y         = padding

        # Month header bar
        if language == "cs":
            header = f"{MONTH_FULL_CS[month_start.month]} {month_start.year}"
        else:
            header = f"{MONTH_FULL_EN[month_start.month]} {month_start.year}"

        hdr_h = font_size + 12
        draw.rectangle([0, y, width, y + hdr_h], fill=hdr_bg)
        draw.text((width // 2, y + hdr_h // 2), header,
                  font=bold, fill=hdr_fg, anchor="mm")
        y += hdr_h + 4

        # Day-of-week header row
        col_w = (width - 2 * padding) // 7
        for i, name in enumerate(day_short):
            x = padding + i * col_w + col_w // 2
            draw.text((x, y + font_size // 2), name,
                      font=medium, fill=fg, anchor="mm")
        y += font_size + 6
        draw.line([(padding, y), (width - padding, y)], fill=fg, width=1)
        y += 4

        # Grid
        available_h = height - y - padding
        row_h       = max(font_size + 10, available_h // 6)
        col         = month_start.weekday()
        row_y       = y
        current     = month_start

        while current <= month_end:
            cell_x = padding + col * col_w

            if current == today:
                draw.rectangle(
                    [cell_x+1, row_y+1, cell_x+col_w-2, row_y+row_h-2],
                    fill=hdr_bg)
                num_color = hdr_fg
            else:
                num_color = fg

            draw.text(
                (cell_x + col_w // 2, row_y + font_size // 2 + 2),
                str(current.day), font=medium, fill=num_color, anchor="mm")

            # Event pills (up to 2 per cell)
            if current in events:
                ey = row_y + font_size + 6
                for ev in events[current][:2]:
                    if ey + tiny.size + 2 > row_y + row_h:
                        break
                    pw    = col_w - 4
                    label = self._truncate(draw, ev["summary"], tiny, pw - 4)
                    self._draw_rounded_rect(draw, cell_x+2, ey,
                                            cell_x+2+pw, ey+tiny.size+2,
                                            radius=2, fill=ev["fill_rgb"])
                    draw.rounded_rectangle(
                        [cell_x+2, ey, cell_x+2+pw, ey+tiny.size+2],
                        radius=2, outline=ev["border_rgb"], width=1)
                    draw.text((cell_x+4, ey+1), label, font=tiny, fill=fg)
                    ey += tiny.size + 3

            col += 1
            if col > 6:
                col    = 0
                row_y += row_h
            current += timedelta(days=1)

        return img

    # ------------------------------------------------------------------ #
    #  Agenda renderer                                                     #
    # ------------------------------------------------------------------ #

    def _render_agenda(self, width, height, today, events,
                       font_size, language, colors):
        bg      = colors["cal_bg"]
        fg      = colors["cal_text"]
        hdr_bg  = colors["cal_header_bg"]
        hdr_fg  = colors["cal_header_text"]

        img  = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)
        bold, medium, small, tiny = self._load_fonts(font_size)

        padding    = 10
        indent     = padding + 6
        pill_r     = 4
        pill_pad_x = 6
        pill_pad_y = 3
        event_gap  = 3
        date_h     = font_size + 10
        sep_gap    = 6
        y          = padding

        sample_time_w = int(draw.textlength("00:00–00:00", font=small))
        time_w        = sample_time_w + pill_pad_x
        summary_x     = indent + time_w + pill_pad_x + 4

        # Panel header
        header = "Nadcházející události" if language == "cs" else "Upcoming events"
        hdr_h  = font_size + 12
        draw.rectangle([0, y, width, y + hdr_h], fill=hdr_bg)
        draw.text((width // 2, y + hdr_h // 2), header,
                  font=bold, fill=hdr_fg, anchor="mm")
        y += hdr_h + 6

        if not events:
            msg = "Žádné události" if language == "cs" else "No upcoming events"
            draw.text((padding, y), msg, font=small, fill=fg)
            return img

        last_date = None
        pill_h    = font_size + pill_pad_y * 2

        for ev in events:
            # Day header
            if ev["date"] != last_date:
                if last_date is not None:
                    y += sep_gap
                    if y + date_h > height - padding:
                        break
                    draw.line([(padding, y), (width - padding, y)],
                              fill=fg, width=1)
                    y += sep_gap

                if y + date_h > height - padding:
                    break

                d = ev["date"]
                if language == "cs":
                    label = f"{DAY_FULL_CS[d.isoweekday()]}  {d.day}. {MONTH_FULL_CS[d.month]}"
                else:
                    label = f"{DAY_FULL_EN[d.weekday()]}  {d.day} {MONTH_FULL_EN[d.month]}"

                if d == today:
                    draw.rectangle(
                        [padding-2, y, width-padding, y+date_h-2],
                        fill=hdr_bg)
                    draw.text((padding+4, y + date_h // 2), label,
                              font=bold, fill=hdr_fg, anchor="lm")
                else:
                    draw.text((padding, y + date_h // 2), label,
                              font=bold, fill=fg, anchor="lm")

                y        += date_h
                last_date = ev["date"]

            # Event pill
            if y + pill_h + pill_pad_y > height - padding:
                break

            px0, px1 = indent, width - padding
            py0, py1 = y, y + pill_h

            self._draw_rounded_rect(draw, px0, py0, px1, py1,
                                    radius=pill_r, fill=ev["fill_rgb"])
            draw.rounded_rectangle([px0, py0, px1, py1],
                                   radius=pill_r, outline=ev["border_rgb"], width=2)

            if ev["time_str"]:
                draw.text((px0 + pill_pad_x, py0 + pill_h // 2),
                          ev["time_str"], font=small, fill=fg, anchor="lm")

            summary = self._truncate(draw, ev["summary"], small,
                                     px1 - summary_x - pill_pad_x)
            draw.text((summary_x, py0 + pill_h // 2),
                      summary, font=small, fill=fg, anchor="lm")

            y += pill_h + event_gap

        return img
