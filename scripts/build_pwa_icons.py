"""Make the app's install icons from the logo (needs rsvg-convert: apt install librsvg2-bin).

Usage: python scripts/build_pwa_icons.py   ->  app/static/img/pwa/*.png
* icon-192/512        the rounded logo on a transparent background ("any" purpose)
* maskable-192/512    full-bleed gradient with the mark inside the central safe zone, for Android's shaped icons
* apple-touch-icon    180x180, square and opaque (iOS rounds the corners itself)
* favicon-32          for browsers that don't take the SVG favicon
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "app/static/img/pwa"
GRADIENT = ('<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0D9488"/>'
            '<stop offset="1" stop-color="#2563EB"/></linearGradient></defs>')
MARK = ('<path d="M12 40 C20 24, 44 24, 52 40" fill="none" stroke="#fff" stroke-width="4.5" stroke-linecap="round"/>'
        '<path d="M10 44 H54" stroke="#fff" stroke-width="4.5" stroke-linecap="round"/>'
        '<path d="M22 44 V33 M32 44 V29.5 M42 44 V33" stroke="#fff" stroke-width="3.5" stroke-linecap="round" '
        'opacity=".85"/><circle cx="32" cy="17" r="4" fill="#fff"/>')


def svg(rounded: bool, scale: float) -> str:
    """scale < 1 shrinks the mark towards the centre (safe zone for maskable / iOS icons)."""
    offset = 32 * (1 - scale)
    lift = 2.4 * scale if scale < 1 else 0  # the mark sits a little low in the logo; centre it when shrunk
    bg = '<rect width="64" height="64" rx="16" fill="url(#g)"/>' if rounded else '<rect width="64" height="64" fill="url(#g)"/>'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">{GRADIENT}{bg}'
            f'<g transform="translate({offset:.2f} {offset + lift:.2f}) scale({scale})">{MARK}</g></svg>')


def render(source: str, size: int, name: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False) as fh:
        fh.write(source)
    subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), "-o", str(OUT / name), fh.name], check=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        render(svg(True, 1.0), size, f"icon-{size}.png")
        render(svg(False, 0.72), size, f"maskable-{size}.png")  # mark fits inside the 80% safe circle
    render(svg(False, 0.82), 180, "apple-touch-icon.png")
    render(svg(True, 1.0), 32, "favicon-32.png")
    print("Wrote", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))


if __name__ == "__main__":
    main()
