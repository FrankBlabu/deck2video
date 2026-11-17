// deck2video: draw the Slides format's own elements, <x-shape> and <x-connector>, which a
// plain browser does not know. Called once after load; it attaches a shadow SVG to each
// element at render time and never changes the slide file.
(() => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const svgEl = (name, attrs) => {
    const el = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
    return el;
  };
  const num = (el, name) => {
    const v = el.getAttribute(name);
    return v === null || v === "" ? null : Number.parseFloat(v);
  };

  // Block arrow pointing right in a unit box; the head is the last 40 % of the length.
  const ARROW = [[0, 0.3], [0.6, 0.3], [0.6, 0], [1, 0.5], [0.6, 1], [0.6, 0.7], [0, 0.7]];
  const MAP = {
    "arrow-right": ([x, y]) => [x, y],
    "arrow-left": ([x, y]) => [1 - x, y],
    "arrow-down": ([x, y]) => [y, x],
    "arrow-up": ([x, y]) => [y, 1 - x],
  };

  function drawShape(host) {
    const cs = getComputedStyle(host);
    const fill = cs.backgroundColor;
    const strokeW = Number.parseFloat(cs.borderTopWidth) || 0;
    const stroke = cs.borderTopStyle !== "none" && strokeW > 0 ? cs.borderTopColor : "none";
    const w = host.offsetWidth || Number.parseFloat(cs.width) || 0;
    const h = host.offsetHeight || Number.parseFloat(cs.height) || 0;
    host.style.setProperty("background", "transparent", "important");
    host.style.setProperty("border-color", "transparent", "important");
    if (cs.display === "inline") host.style.display = "block";
    const svg = svgEl("svg", { width: w, height: h, viewBox: `0 0 ${w} ${h}` });
    svg.style.cssText = "position:absolute;left:0;top:0;overflow:visible";
    const paint = { fill, stroke, "stroke-width": strokeW };
    const kind = host.getAttribute("kind") || "ellipse";
    let shape;
    if (kind === "ellipse") {
      shape = svgEl("ellipse", { cx: w / 2, cy: h / 2, rx: w / 2, ry: h / 2, ...paint });
    } else if (kind === "diamond") {
      shape = svgEl("polygon", { points: `${w / 2},0 ${w},${h / 2} ${w / 2},${h} 0,${h / 2}`, ...paint });
    } else if (kind in MAP) {
      const pts = ARROW.map(MAP[kind]).map(([x, y]) => `${x * w},${y * h}`).join(" ");
      shape = svgEl("polygon", { points: pts, ...paint });
    } else if (kind === "line") {
      shape = svgEl("line", {
        x1: 0, y1: h / 2, x2: w, y2: h / 2,
        stroke: stroke !== "none" ? stroke : fill, "stroke-width": Math.max(strokeW, 2),
      });
    } else {
      return;
    }
    svg.appendChild(shape);
    if (cs.position === "static") host.style.position = "relative";
    host.attachShadow({ mode: "open" }).appendChild(svg);
  }

  function head(svg, [x, y], [fx, fy], kind, color, width) {
    if (kind === "none") return;
    const len = 6 + width * 3;
    const ang = Math.atan2(y - fy, x - fx);
    const a = ang + Math.PI - 0.45;
    const b = ang + Math.PI + 0.45;
    const p1 = [x + len * Math.cos(a), y + len * Math.sin(a)];
    const p2 = [x + len * Math.cos(b), y + len * Math.sin(b)];
    if (kind === "open") {
      svg.appendChild(svgEl("polyline", {
        points: `${p1} ${x},${y} ${p2}`, fill: "none", stroke: color,
        "stroke-width": width, "stroke-linejoin": "round", "stroke-linecap": "round",
      }));
    } else {
      svg.appendChild(svgEl("polygon", { points: `${p1} ${x},${y} ${p2}`, fill: color }));
    }
  }

  function drawConnector(host) {
    const cs = getComputedStyle(host);
    const color = cs.color;
    const width = Number.parseFloat(cs.borderTopWidth) || 2;
    const style = cs.borderTopStyle;
    host.style.setProperty("border-style", "none", "important");
    const coords = ["x1", "y1", "x2", "y2"].map((k) => num(host, k));
    let pts;
    const svg = svgEl("svg", { width: 1, height: 1 });
    svg.style.cssText = "position:absolute;left:0;top:0;overflow:visible";
    if (coords.every((v) => v !== null)) {
      const [x1, y1, x2, y2] = coords;
      host.style.cssText += ";position:absolute;left:0;top:0;width:0;height:0;overflow:visible";
      const route = host.getAttribute("route") || "straight";
      if (route === "hv") pts = [[x1, y1], [x2, y1], [x2, y2]];
      else if (route === "vh") pts = [[x1, y1], [x1, y2], [x2, y2]];
      else if (route === "elbow") {
        const xm = (x1 + x2) / 2;
        pts = [[x1, y1], [xm, y1], [xm, y2], [x2, y2]];
      } else pts = [[x1, y1], [x2, y2]];
    } else {
      if (cs.display === "inline") host.style.display = "block";
      if (cs.position === "static") host.style.position = "relative";
      const w = host.offsetWidth;
      const h = host.offsetHeight;
      const from = host.getAttribute("from") || "tl";
      const sx = from.endsWith("r") ? w : 0;
      const sy = from.startsWith("b") ? h : 0;
      pts = [[sx, sy], [w - sx, h - sy]];
    }
    const dash = style === "dashed" ? `${width * 3} ${width * 2}` : style === "dotted" ? `0 ${width * 2}` : null;
    const line = svgEl("polyline", {
      points: pts.map((p) => p.join(",")).join(" "), fill: "none", stroke: color,
      "stroke-width": width, "stroke-linejoin": "round",
      "stroke-linecap": style === "dotted" ? "round" : "butt",
    });
    if (dash) line.setAttribute("stroke-dasharray", dash);
    svg.appendChild(line);
    const headAttr = host.getAttribute("head") || "end";
    const startKind = host.getAttribute("head-start") || (headAttr === "both" ? "filled" : "none");
    const endKind = host.getAttribute("head-end") || (headAttr === "none" ? "none" : "filled");
    head(svg, pts[pts.length - 1], pts[pts.length - 2], endKind, color, width);
    head(svg, pts[0], pts[1], startKind, color, width);
    host.attachShadow({ mode: "open" }).appendChild(svg);
  }

  window.__d2vDrawShapes = () => {
    for (const el of document.querySelectorAll("x-shape")) if (!el.shadowRoot) drawShape(el);
    for (const el of document.querySelectorAll("x-connector")) if (!el.shadowRoot) drawConnector(el);
  };
})();
