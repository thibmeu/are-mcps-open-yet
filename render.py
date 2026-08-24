#!/usr/bin/env -S uv run
# /// script
# dependencies = []
# ///
"""Render data/site/*.json into bespoke inline SVG panels.

No chart library and no inline colours: every mark carries a CSS class and the
theme token layer (assets/css/mcp-viz.css) decides how it looks in light, dark
and auto. Panels are viewBox-based so they scale to the column width.

Light-mode aqua and yellow sit below 3:1 on this theme's surface, so the palette
is only legal with visible direct labels -- every mark here is labelled, and that
is a requirement rather than a stylistic choice.

  ./render.py   ->  data/site/svg/*.svg
"""
import html
import json
import pathlib

SITE = pathlib.Path("data/site")
OUT = SITE / "svg"
W = 720  # viewBox width; height varies per panel
MOBILE_W = 360


def load(name: str):
    return json.loads((SITE / f"{name}.json").read_text())


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def svg(body: str, height: int, title: str, desc: str = "", width: int = W) -> str:
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}" '
        f'preserveAspectRatio="xMinYMin meet">'
        f"<title>{esc(title)}</title>"
        + (f"<desc>{esc(desc)}</desc>" if desc else "")
        + body
        + "</svg>"
    )


def legend(items: list[tuple[str, str]], x: int = 150) -> tuple[str, int]:
    """Identity is never colour-alone: a legend accompanies every multi-series
    panel (single-series panels get none -- the title names them).

    Drawn INSIDE the viewBox rather than as sibling HTML, so every emitted file
    stays a valid standalone SVG and the shortcode can treat them uniformly.
    Returns (markup, height consumed).
    """
    parts, cx = [], x
    for lbl, cls in items:
        parts.append(
            f'<rect class="bar {cls}" x="{cx}" y="2" width="10" height="10"/>'
            f'<text class="label-muted" x="{cx + 16}" y="11">{esc(lbl)}</text>'
        )
        cx += 20 + len(lbl) * 7
    return "".join(parts), 22


def hbars(rows, label_key, value_key, cls_for, title, desc="", sub_key=None):
    """Horizontal bars: the right form when categories have long names."""
    pad_l, pad_r, top, row_h, gap = 190, 90, 8, 30, 10
    vmax = max(r[value_key] for r in rows) or 1
    plot = W - pad_l - pad_r
    body = []
    for i, r in enumerate(rows):
        y = top + i * (row_h + gap)
        w = max(2, round(plot * r[value_key] / vmax))
        body.append(
            f'<g class="step">'
            f'<text class="label" x="{pad_l - 10}" y="{y + row_h * 0.68}" '
            f'text-anchor="end">{esc(r[label_key])}</text>'
            f'<rect class="bar {cls_for(i, r)}" x="{pad_l}" y="{y}" '
            f'width="{w}" height="{row_h}"/>'
            f'<text class="value" x="{pad_l + w + 8}" y="{y + row_h * 0.68}">'
            f'{r[value_key]:,}</text>'
            f"</g>"
        )
    h = top + len(rows) * (row_h + gap)
    return svg("".join(body), h, title, desc)


def grouped(rows, label_key, a_key, b_key, title, desc="", legend_items=None):
    """Two series on ONE shared axis -- never a second scale."""
    pad_l, pad_r, row_h, gap = 150, 80, 15, 14
    head, top = ("", 8)
    if legend_items:
        head, lh = legend(legend_items, pad_l)
        top = 8 + lh
    vmax = max(max(r[a_key], r[b_key]) for r in rows) or 1
    plot = W - pad_l - pad_r
    body = []
    for i, r in enumerate(rows):
        y = top + i * (row_h * 2 + gap)
        for j, (key, cls) in enumerate(((a_key, "bar-1"), (b_key, "bar-2"))):
            w = max(2, round(plot * r[key] / vmax))
            yy = y + j * row_h
            body.append(
                f'<rect class="bar {cls}" x="{pad_l}" y="{yy}" width="{w}" height="{row_h}"/>'
                f'<text class="value" x="{pad_l + w + 8}" y="{yy + row_h * 0.8}">'
                f"{r[key]:,}</text>"
            )
        body.append(
            f'<text class="label" x="{pad_l - 10}" y="{y + row_h}" '
            f'text-anchor="end">{esc(r[label_key])}</text>'
        )
    h = top + len(rows) * (row_h * 2 + gap)
    return svg(head + "".join(body), h, title, desc)


def stacked_pct(groups, title, desc, legend_items):
    """100% stacked bars: composition within each group.

    The question is "what fraction of each posture holds sensitive data", so the
    bars are normalised -- absolute counts live in the labels, which is also the
    relief the light-mode palette requires.
    """
    pad_l, pad_r, row_h, gap = 150, 60, 34, 16
    head, top = legend(legend_items, pad_l)
    top += 8
    plot = W - pad_l - pad_r
    body = []
    for i, (group, segs) in enumerate(groups):
        y = top + i * (row_h + gap)
        total = sum(v for _, v, _ in segs) or 1
        x = pad_l
        body.append(
            f'<text class="label" x="{pad_l - 10}" y="{y + row_h * 0.62}" '
            f'text-anchor="end">{esc(group)}</text>'
            f'<text class="label-muted" x="{pad_l - 10}" y="{y + row_h * 0.62 + 15}" '
            f'text-anchor="end">{total:,} servers</text>'
        )
        for lbl, val, cls in segs:
            w = plot * val / total
            pct = 100 * val / total
            body.append(
                f'<rect class="bar {cls}" x="{x:.1f}" y="{y}" '
                f'width="{max(w, 0.5):.1f}" height="{row_h}"/>'
            )
            # Only label segments with room; the legend carries the rest.
            if pct >= 9:
                body.append(
                    f'<text class="value" x="{x + w / 2:.1f}" y="{y + row_h * 0.62}" '
                    f'text-anchor="middle">{pct:.0f}%</text>'
                )
            x += w
    h = top + len(groups) * (row_h + gap)
    return svg(head + "".join(body), h, title, desc)


def grouped_mobile(rows, label_key, a_key, b_key, title, desc=""):
    """Narrow grouped bars with labels above the marks, not beside them."""
    top, row_h, gap, bar_h = 34, 49, 6, 12
    plot, value_gap = 292, 7
    vmax = max(max(r[a_key], r[b_key]) for r in rows) or 1
    body = [
        '<rect class="bar bar-1" x="0" y="2" width="10" height="10"/>'
        '<text class="label-muted" x="16" y="11">endpoints</text>'
        '<rect class="bar bar-2" x="122" y="2" width="10" height="10"/>'
        '<text class="label-muted" x="138" y="11">hosts</text>'
    ]
    for i, r in enumerate(rows):
        y = top + i * (row_h + gap)
        body.append(f'<g class="step"><text class="label" x="0" y="{y}">{esc(r[label_key])}</text>')
        for j, (key, cls) in enumerate(((a_key, "bar-1"), (b_key, "bar-2"))):
            yy = y + 9 + j * (bar_h + 4)
            width = max(2, round(plot * r[key] / vmax))
            body.append(
                f'<rect class="bar {cls}" x="0" y="{yy}" width="{width}" height="{bar_h}"/>'
                f'<text class="value" x="{width + value_gap}" y="{yy + 10}">{r[key]:,}</text>'
            )
        body.append('</g>')
    height = top + len(rows) * (row_h + gap)
    return svg("".join(body), height, title, desc, width=MOBILE_W)


def hbars_mobile(rows, label_key, value_key, cls_for, title, desc=""):
    """Narrow ranked bars; long labels get their own line above each mark."""
    top, row_h, gap, bar_h = 8, 38, 8, 18
    plot, value_gap = 284, 8
    vmax = max(r[value_key] for r in rows) or 1
    body = []
    for i, r in enumerate(rows):
        y = top + i * (row_h + gap)
        width = max(2, round(plot * r[value_key] / vmax))
        body.append(
            f'<g class="step">'
            f'<text class="label" x="0" y="{y + 14}">{esc(r[label_key])}</text>'
            f'<rect class="bar {cls_for(i, r)}" x="0" y="{y + 20}" width="{width}" height="{bar_h}"/>'
            f'<text class="value" x="{width + value_gap}" y="{y + 34}">{r[value_key]:,}</text>'
            f'</g>'
        )
    height = top + len(rows) * (row_h + gap)
    return svg("".join(body), height, title, desc, width=MOBILE_W)


def stacked_pct_mobile(groups, title, desc, legend_items):
    """Narrow compositions with a wrapped legend and labels above each bar."""
    plot, bar_h = 352, 34
    body = []
    for i, (label, cls) in enumerate(legend_items):
        x = (i % 2) * 180
        y = (i // 2) * 22 + 2
        body.append(
            f'<rect class="bar {cls}" x="{x}" y="{y}" width="10" height="10"/>'
            f'<text class="label-muted" x="{x + 16}" y="{y + 9}">{esc(label)}</text>'
        )
    top, row_h, gap = 64, 50, 10
    for i, (group, segs) in enumerate(groups):
        y = top + i * (row_h + gap)
        total = sum(value for _, value, _ in segs) or 1
        body.append(
            f'<text class="label" x="0" y="{y}">{esc(group)}</text>'
            f'<text class="label-muted" x="{plot}" y="{y}" text-anchor="end">{total:,} servers</text>'
        )
        x = 0.0
        for _, value, cls in segs:
            width = plot * value / total
            pct = 100 * value / total
            body.append(
                f'<rect class="bar {cls}" x="{x:.1f}" y="{y + 10}" '
                f'width="{max(width, 0.5):.1f}" height="{bar_h}"/>'
            )
            if pct >= 11:
                body.append(
                    f'<text class="value" x="{x + width / 2:.1f}" y="{y + 32}" '
                    f'text-anchor="middle">{pct:.0f}%</text>'
                )
            x += width
    height = top + len(groups) * (row_h + gap)
    return svg("".join(body), height, title, desc, width=MOBILE_W)


def access_journey(rows):
    """A 100-mark editorial diagram for the scroll-driven lead visual.

    Each mark represents roughly one percent of probed endpoints. Marks keep a
    stable identity while CSS reveals the connection, auth, and discovery
    thresholds. Exact counts remain attached to the groups; the dots communicate
    composition, not a second false-precision scale.
    """
    order = ["broken", "required", "paywalled", "optional", "open",
             "partial", "throttled"]
    counts = {r["posture"]: r["endpoints"] for r in rows}
    total = sum(counts.values())

    # Largest-remainder allocation makes the diagram exactly 100 marks.
    exact = {k: 100 * counts.get(k, 0) / total for k in order}
    dots = {k: int(exact[k]) for k in order}
    for k in sorted(order, key=lambda k: exact[k] - dots[k], reverse=True)[:100-sum(dots.values())]:
        dots[k] += 1

    # Horizontal position says how the endpoint presents to an anonymous client.
    # Partial/throttled remain explicitly unresolved rather than being forced into
    # either side of the gate.
    columns = {
        "broken": 116, "required": 330, "paywalled": 330,
        "optional": 550, "open": 780, "partial": 550, "throttled": 550,
    }
    groups = [
        ("broken",), ("required", "paywalled"),
        ("optional", "partial", "throttled"), ("open",)
    ]
    parts = [
        '<g class="journey-guides">',
        '<line class="threshold threshold-connect" x1="228" y1="92" x2="228" y2="486"/>',
        '<line class="threshold threshold-auth" x1="432" y1="92" x2="432" y2="486"/>',
        '<line class="threshold threshold-tools" x1="640" y1="92" x2="640" y2="486"/>',
        '<text class="eyebrow" x="228" y="66" text-anchor="middle">CONNECT</text>',
        '<text class="eyebrow" x="432" y="66" text-anchor="middle">INITIALIZE</text>',
        '<text class="eyebrow" x="640" y="66" text-anchor="middle">TOOLS / LIST</text>',
        '</g>',
        '<g class="client" transform="translate(22 269)">',
        '<path class="client-mark" d="M0 14h48m-9-9 9 9-9 9"/>',
        '<text class="label" x="0" y="-2">anonymous client</text>',
        '</g>',
        '<g class="endpoint-field">',
    ]
    mark_i = 0
    for keys in groups:
        keys_total = sum(dots[k] for k in keys)
        cols = 4 if keys_total < 20 else 6
        local_i = 0
        for key in keys:
            for _ in range(dots[key]):
                # All marks begin in the same neutral 10×10 population field.
                sx = 260 + (mark_i % 10) * 36
                sy = 105 + (mark_i // 10) * 36
                # Final positions form readable posture clusters.
                gx = columns[key]
                fx = gx + (local_i % cols) * 26 - ((cols - 1) * 26 / 2)
                fy = 145 + (local_i // cols) * 28
                parts.append(
                    f'<circle class="endpoint endpoint-{key}" cx="{sx}" cy="{sy}" r="11" '
                    f'style="--dx:{fx-sx:.1f}px;--dy:{fy-sy:.1f}px"/>'
                )
                mark_i += 1
                local_i += 1
    parts.append('</g><g class="journey-labels">')
    labels = [
        (116, "did not answer", counts["broken"]),
        (330, "auth required", counts["required"]),
        (550, "auth advertised", counts["optional"]),
        (780, "simply open", counts["open"]),
    ]
    for x, label, count in labels:
        pct = f"{100 * count / total:.1f}%"
        parts.append(
            f'<g class="outcome outcome-{label.replace(" ", "-")}">'
            f'<text class="outcome-value" x="{x}" y="520" text-anchor="middle">{pct}</text>'
            f'<text class="label" x="{x}" y="542" text-anchor="middle">{esc(label)}</text>'
            f'<text class="label-muted" x="{x}" y="561" text-anchor="middle">{count:,} endpoints</text>'
            '</g>'
        )
    parts.append('</g>')
    return svg("".join(parts), 580,
               "How far an anonymous client gets across the MCP registry",
               "One hundred marks represent the 11,121 probed remote endpoints. "
               "They separate into endpoints that did not answer, require authentication, "
               "advertise authentication, or are simply open through tools/list.",
               width=900)


def access_journey_mobile(rows):
    """Mobile companion to access_journey.

    A phone cannot show three labelled protocol thresholds and a narrative card
    side by side. Keep the 100 stable endpoint marks, but use the full width for
    a compact 10x10 field. Scroll state still highlights the same categories.
    """
    order = ["broken", "required", "paywalled", "optional", "open",
             "partial", "throttled"]
    counts = {r["posture"]: r["endpoints"] for r in rows}
    total = sum(counts.values())
    exact = {k: 100 * counts.get(k, 0) / total for k in order}
    dots = {k: int(exact[k]) for k in order}
    for k in sorted(order, key=lambda k: exact[k] - dots[k], reverse=True)[:100-sum(dots.values())]:
        dots[k] += 1

    parts = ['<g class="endpoint-field endpoint-field-mobile">']
    i = 0
    for key in order:
        for _ in range(dots[key]):
            x = 78 + (i % 10) * 23
            y = 12 + (i // 10) * 23
            parts.append(
                f'<circle class="endpoint endpoint-{key}" cx="{x}" cy="{y}" r="7"/>'
            )
            i += 1
    parts.append('</g>')
    return svg("".join(parts), 234,
               "Anonymous access outcomes across the MCP registry",
               "One hundred marks represent the 11,121 remote endpoints. "
               "Scroll steps highlight endpoints that did not answer, require "
               "authentication, advertise authentication, or are simply open.",
               width=360)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    written = []

    # Lead visual: a persistent population transformed by scroll steps on the site.
    written.append(("access_journey", access_journey(load("posture"))))
    written.append(("access_journey_mobile", access_journey_mobile(load("posture"))))

    # 1. Shape -- one series, so no legend.
    shape = load("shape")
    written.append(("shape", hbars(
        shape, "shape", "servers", lambda i, r: "bar-1",
        "Registry shape: local, remote, hybrid, undeployable",
        "Every server in the registry at its latest version, by how it is deployed.")))

    # 2. Gate-depth ladder -- ordinal magnitude, so a sequential ramp, and the
    #    only panel that gets scroll reveals.
    ladder = load("ladder")
    written.append(("ladder", hbars(
        ladder, "rung", "endpoints", lambda i, r: f"seq-{min(i + 2, 5)}",
        "Gate depth: how far an anonymous client gets",
        "Remote endpoints by the furthest point an unauthenticated client reaches.")))

    # 3. Posture, both counting units on a shared axis.
    posture = load("posture")
    written.append(("posture", grouped(
        posture, "posture", "endpoints", "hosts",
        "Auth posture, by endpoint and by host",
        "The two units disagree because a few operators register many endpoints.",
        legend_items=[("endpoints", "bar-1"), ("hosts", "bar-2")])))
    written.append(("posture_mobile", grouped_mobile(
        posture, "posture", "endpoints", "hosts",
        "Auth posture, by endpoint and by host",
        "The two units disagree because a few operators register many endpoints.")))

    # 4. Concentration -- single accent on the outlier, neutral for the rest.
    conc = load("concentration")[:8]
    written.append(("concentration", hbars(
        conc, "host", "endpoints",
        lambda i, r: "bar-accent" if i == 0 else "bar-1",
        "Endpoint concentration by host",
        "One host accounts for about an eighth of every remote endpoint.")))
    written.append(("concentration_mobile", hbars_mobile(
        conc, "host", "endpoints",
        lambda i, r: "bar-accent" if i == 0 else "bar-1",
        "Endpoint concentration by host",
        "One host accounts for about an eighth of every remote endpoint.")))

    # 5. Client-id path -- can an unattended agent get in at all?
    cid = [r for r in load("client_id_path") if r["client_id_path"]]
    written.append(("client_id_path", hbars(
        cid, "client_id_path", "auth_servers",
        lambda i, r: "bar-accent" if r["client_id_path"] == "dcr" else "bar-1",
        "How a client can obtain a client_id",
        "DCR was deprecated on 2026-07-28, 17 days before this snapshot.")))

    # 6. Protocol lag.
    proto = load("protocol")
    written.append(("protocol", hbars(
        proto, "protocol", "servers",
        lambda i, r: "bar-accent" if r["protocol"] == "2026-07-28" else "bar-1",
        "Protocol version negotiated",
        "The current specification is 2026-07-28.")))

    # 7. Sensitivity composition per posture. This is the only panel that
    #    depends on the model-assisted labelling pass.
    SENS = [("public", "bar-1"), ("user-private", "bar-2"),
            ("financial", "bar-3"), ("infrastructure-control", "bar-4")]
    sens = load("sensitivity")
    groups = []
    for posture in ("required", "optional", "open"):
        segs = []
        for lbl, cls in SENS:
            n = sum(r["servers"] for r in sens
                    if r["posture"] == posture and r["sensitivity"] == lbl)
            segs.append((lbl, n, cls))
        if sum(v for _, v, _ in segs):
            groups.append((posture, segs))
    written.append(("sensitivity", stacked_pct(
        groups,
        "What sits behind each posture",
        "Servers by the highest-sensitivity capability inferred from their metadata. "
        "Required-auth servers never show a tool list, so their labels rest on name "
        "and description alone.",
        SENS)))
    written.append(("sensitivity_mobile", stacked_pct_mobile(
        groups,
        "What sits behind each posture",
        "Servers by the highest-sensitivity capability inferred from their metadata. "
        "Required-auth servers never show a tool list, so their labels rest on name "
        "and description alone.",
        SENS)))

    # 8. The consent ledger. Two near-equal bars is the finding: no amortisation.
    consent = load("consent")[0]
    ledger = [
        {"k": "gated endpoints", "v": consent["gated_endpoints"]},
        {"k": "authorization servers", "v": consent["distinct_issuers"]},
    ]
    written.append(("consent", hbars(
        ledger, "k", "v", lambda i, r: "bar-accent" if i == 1 else "bar-1",
        "Gated endpoints against distinct authorization servers",
        f"{consent['endpoints_per_issuer']} endpoints per issuer: an agent "
        f"amortises almost nothing across the ecosystem.")))

    for name, markup in written:
        (OUT / f"{name}.svg").write_text(markup)
    print(f"wrote {len(written)} panels to {OUT}")
    for name, markup in written:
        print(f"  {name}.svg  {len(markup):,} bytes")


if __name__ == "__main__":
    main()
