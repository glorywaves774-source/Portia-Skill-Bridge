"""Build the inline Lucide icon sprite used by the templates.

Usage: python scripts/build_icons.py <path-to-lucide-static/icons>
(Get the icons with `npm pack lucide-static && tar xzf lucide-static-*.tgz`.)
"""
import pathlib
import re
import sys

ICONS = [
    "layout-dashboard", "building-2", "credit-card", "scroll-text", "menu", "x", "sun", "moon", "search",
    "arrow-left", "check", "graduation-cap", "briefcase", "award", "chart-column", "users", "shield-check",
    "sparkles", "chevron-right", "activity", "clipboard-check", "user", "layers", "circle-alert",
    "hourglass", "pause", "play", "globe", "arrow-right", "file-text", "circle-check", "info", "settings", "log-out", "log-in", "lock", "mail", "eye", "eye-off", "key-round", "user-plus", "rocket", "circle-help", "message-circle", "send", "chevron-up", "chevron-down", "calendar", "timer", "book-open",
    "handshake", "map-pin", "target", "calendar-check", "user-check", "trending-up", "list-checks", "phone", "file-spreadsheet", "download", "filter", "bell", "shield", "database-backup", "server",
]

src = pathlib.Path(sys.argv[1])
out = pathlib.Path(__file__).resolve().parent.parent / "app/templates/partials/_icons.svg"
parts = ['<svg xmlns="http://www.w3.org/2000/svg" class="icon-sprite" aria-hidden="true" focusable="false">']
for name in ICONS:
    svg = (src / f"{name}.svg").read_text()
    inner = re.sub(r"^.*?<svg[^>]*>|</svg>\s*$", "", svg, flags=re.S).strip()
    inner = re.sub(r"\s*\n\s*", "", inner)
    parts.append(f'<symbol id="i-{name}" viewBox="0 0 24 24">{inner}</symbol>')
parts.append("</svg>")
out.write_text("\n".join(parts) + "\n")
print(f"Wrote {len(ICONS)} icons to {out}")
