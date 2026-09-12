#!/usr/bin/env python3
"""Generate the CanLab documentation site into docs/.

The pages share a masthead, a per-page table of contents and a footer, so they
are rendered from one shell rather than kept in sync by hand. Every claim here
is meant to describe the code on ``main``; when a statement is about a specific
module, the module is named so it can be checked.

    python docs/site_src/build_site.py

Output is committed, because GitHub Pages serves docs/ directly with no build
step (there is a .nojekyll marker so Jekyll leaves it alone).
"""
from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent
REPO = "https://github.com/Sherin-SEF-AI/CanLab"

NAV = [
    ("index.html", "Overview"),
    ("install.html", "Install"),
    ("safety.html", "Safety"),
    ("guide.html", "Guide"),
    ("tabs.html", "Tabs"),
    ("analysis.html", "Analysis"),
    ("diagnostics.html", "Diagnostics"),
    ("exports.html", "Exports"),
    ("integrations.html", "Integrations"),
    ("reference.html", "Reference"),
]

PARTS = [
    ("part1-analysis", "1. Loading a capture and finding structure", "2:58",
     "FRAMES, the ID panel and inspector, SIGNALS, counter and checksum "
     "detection, the checksum guesser, entropy boundaries."),
    ("part2-signals", "2. Defining signals and checking them", "2:37",
     "DBC BUILDER and its bit grid, the live decode preview, PLOT, "
     "INTELLIGENCE, ML INTEL, DASHBOARD."),
    ("part3-outputs", "3. Timeline, code generation and exports", "2:25",
     "TIMELINE, CODE GEN, the five export formats, DIAGNOSTICS, security "
     "access."),
    ("part4-transmitting", "4. The transmit gate, injection and live capture",
     "3:23",
     "ARM TX, INJECTION, replay, fuzzing, GATEWAY, OBD-II, the AI engine, "
     "live capture."),
]


def video_card(slug: str, title: str, length: str, blurb: str) -> str:
    return f"""<div class="video-card">
  <video controls preload="metadata" poster="" playsinline>
    <source src="canlab-demo-{slug}.mp4" type="video/mp4">
    <track kind="captions" srclang="en" label="English" default
           src="canlab-demo-{slug}.vtt">
    Your browser cannot play this video.
    <a href="canlab-demo-{slug}.mp4">Download it instead</a>.
  </video>
  <div class="meta">
    <h3>{title}</h3>
    <p class="len">{length}</p>
    <p>{blurb}</p>
  </div>
</div>"""


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def page(filename: str, title: str, lede: str, body: str) -> str:
    """Render one page: masthead, contents from its own h2s, body, footer."""
    headings = re.findall(r'<h2 id="([^"]+)">(.*?)</h2>', body, re.S)
    toc = "".join(
        f'<a href="#{hid}">{re.sub("<[^>]+>", "", htext).strip()}</a>'
        for hid, htext in headings)
    nav = "".join(
        f'<a href="{href}"{" aria-current=\"page\"" if href == filename else ""}>'
        f"{label}</a>"
        for href, label in NAV)
    aside = (f'<aside class="toc"><h2>On this page</h2><nav>{toc}</nav></aside>'
             if toc else '<aside class="toc"></aside>')

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · CanLab</title>
<meta name="description" content="{lede}">
<link rel="stylesheet" href="assets/site.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="masthead">
  <div class="masthead-inner">
    <a class="brand" href="index.html">CanLab<span>/docs</span></a>
    <nav aria-label="Sections">{nav}</nav>
  </div>
</header>
<div class="wrap">
  <div class="layout">
    {aside}
    <main id="main">
{body}
    </main>
  </div>
</div>
<footer>
  <div class="wrap">
    <p>CanLab is MIT licensed. Author: Sherin Joseph Roy.
       <a href="{REPO}">Source on GitHub</a> ·
       <a href="{REPO}/releases">Releases</a> ·
       <a href="{REPO}/issues">Issues</a></p>
    <p>These pages describe the code on <code>main</code>. Where a statement is
       about a specific module the module is named, so it can be checked
       against the source.</p>
  </div>
</footer>
</body>
</html>
"""


def h2(text: str) -> str:
    return f'<h2 id="{slugify(text)}">{text}</h2>'


def table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return (f'<div class="table-scroll"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def main() -> int:
    import pages                           # content lives next door

    (DOCS / "assets").mkdir(exist_ok=True)
    (DOCS / ".nojekyll").write_text("")    # serve docs/ verbatim, no Jekyll

    entries = pages.build_pages(h2=h2, table=table, video_card=video_card,
                                parts=PARTS, repo=REPO)
    for extra in (pages.build_pages_2, pages.build_pages_3,
                  pages.build_pages_4, pages.build_pages_5):
        entries += extra(h2=h2, table=table, repo=REPO)

    written = []
    for filename, title, lede, body in entries:
        (DOCS / filename).write_text(page(filename, title, lede, body),
                                     encoding="utf-8")
        written.append(filename)

    known = {href for href, _ in NAV}
    missing = known - set(written)
    if missing:
        raise SystemExit(f"navigation points at pages nobody wrote: {missing}")
    print(f"wrote {len(written)} pages into {DOCS}")
    for name in written:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
