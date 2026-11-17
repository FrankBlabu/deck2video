// deck2video effects overlay. Python computes each effect's state for the frame (a pure
// function of virtual time) and calls window.__d2vfx.apply(state). Effects never edit the
// slide file: the camera is a transform on a wrapper, highlights are overlay elements, and
// reveal sets the target children's opacity at render time only.
(() => {
  "use strict";
  const W = 1920;
  const H = 1080;
  const r2 = (v) => Math.round(v * 100) / 100;

  function slideRoot() {
    return document.querySelector("#d2v-camera > section");
  }
  function find(selector) {
    const root = slideRoot();
    const hits = [...root.querySelectorAll(selector)].filter(
      (el) => el.tagName.toLowerCase() !== "aside" && !el.closest("aside"),
    );
    if (hits.length !== 1) {
      throw new Error(`target ${JSON.stringify(selector)} matches ${hits.length} elements in the browser`);
    }
    return hits[0];
  }
  function rect(el) {
    const r = el.getBoundingClientRect();
    return { x: r.left, y: r.top, w: r.width, h: r.height };
  }
  function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
  }

  function cameraFor(spec) {
    if (!spec) return { scale: 1, tx: 0, ty: 0 };
    const box = rect(find(spec.target));
    if (box.w <= 0 || box.h <= 0) throw new Error(`target ${JSON.stringify(spec.target)} has no size`);
    const s = spec.scale;
    const p = spec.progress;
    const fx = spec.focus[0] === null ? box.x + box.w / 2 : box.x + box.w * spec.focus[0];
    const fy = spec.focus[1] === null ? box.y + box.h / 2 : box.y + box.h * spec.focus[1];
    // Move the focus point towards the canvas centre as the camera arrives, keep the slide
    // covering the whole frame.
    const tx = clamp((W / 2) * p + fx * (1 - p) - s * fx, W * (1 - s), 0);
    const ty = clamp((H / 2) * p + fy * (1 - p) - s * fy, H * (1 - s), 0);
    return { scale: s, tx, ty };
  }

  function overlayBox(box, pad) {
    const el = document.createElement("div");
    el.style.position = "absolute";
    el.style.left = `${box.x - pad}px`;
    el.style.top = `${box.y - pad}px`;
    el.style.width = `${box.w + 2 * pad}px`;
    el.style.height = `${box.h + 2 * pad}px`;
    el.style.boxSizing = "border-box";
    el.style.borderRadius = "12px";
    return el;
  }

  const revealed = new Set();

  window.__d2vfx = {
    apply(state) {
      const camera = document.getElementById("d2v-camera");
      const overlay = document.getElementById("d2v-overlay");
      overlay.replaceChildren();
      camera.style.transform = "none";
      const cam = cameraFor(state.camera);
      camera.style.transform =
        cam.scale === 1 && cam.tx === 0 && cam.ty === 0
          ? "none"
          : `translate(${cam.tx}px, ${cam.ty}px) scale(${cam.scale})`;

      for (const fx of state.reveals) {
        const kids = [...find(fx.target).children];
        kids.forEach((kid, k) => {
          const o = k < fx.opacities.length ? fx.opacities[k] : 1;
          kid.style.setProperty("opacity", String(o), "important");
        });
        revealed.add(fx.target);
      }

      const boxes = {};
      for (const fx of state.effects) {
        const el = find(fx.target);
        const box = rect(el);
        if (box.w <= 0 || box.h <= 0) throw new Error(`target ${JSON.stringify(fx.target)} has no size`);
        boxes[fx.key] = [r2(box.x), r2(box.y), r2(box.w), r2(box.h)];
        if (fx.effect === "spotlight") {
          const hole = overlayBox(box, fx.padding * cam.scale);
          hole.style.boxShadow = `0 0 0 4000px rgba(0, 0, 0, ${fx.dim * fx.progress})`;
          overlay.appendChild(hole);
        } else if (fx.effect === "outline") {
          const ring = overlayBox(box, fx.padding * cam.scale);
          ring.style.border = `${fx.width}px solid ${fx.color}`;
          ring.style.opacity = String(fx.progress);
          overlay.appendChild(ring);
        } else if (fx.effect === "pulse") {
          const ring = overlayBox(box, fx.padding * cam.scale);
          ring.style.border = `5px solid ${fx.color}`;
          ring.style.transform = `scale(${1 + 0.12 * fx.wave})`;
          ring.style.opacity = String(fx.progress * (1 - fx.wave));
          overlay.appendChild(ring);
        }
      }
      return { camera: { scale: r2(cam.scale), tx: r2(cam.tx), ty: r2(cam.ty) }, boxes };
    },
  };
})();
