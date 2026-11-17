// deck2video virtual clock. Injected before any slide markup is parsed.
// Time only moves when Python calls window.__d2v.step(t_ms): every Web Animation
// (CSS animations, transitions, element.animate) and every SVG/SMIL timeline is paused
// and set to t; requestAnimationFrame, timers, performance.now and Date follow t.
(() => {
  "use strict";
  const RealDate = Date;
  const EPOCH = 1767225600000; // 2026-01-01T00:00:00Z, fixed so builds are reproducible
  let now = 0;
  const rafQueue = new Map();
  let rafId = 0;
  const timers = new Map();
  let timerId = 0;
  const firstSeen = new WeakMap();

  performance.now = () => now;
  class VirtualDate extends RealDate {
    constructor(...args) {
      if (args.length === 0) super(EPOCH + now);
      else super(...args);
    }
    static now() {
      return EPOCH + now;
    }
  }
  window.Date = VirtualDate;
  window.requestAnimationFrame = (cb) => {
    rafId += 1;
    rafQueue.set(rafId, cb);
    return rafId;
  };
  window.cancelAnimationFrame = (id) => rafQueue.delete(id);
  const addTimer = (cb, ms, args, repeat) => {
    timerId += 1;
    const delay = Math.max(0, Number(ms) || 0);
    timers.set(timerId, { at: now + delay, cb, args, every: repeat ? Math.max(1, delay) : null });
    return timerId;
  };
  window.setTimeout = (cb, ms, ...args) => addTimer(cb, ms, args, false);
  window.setInterval = (cb, ms, ...args) => addTimer(cb, ms, args, true);
  window.clearTimeout = (id) => timers.delete(id);
  window.clearInterval = (id) => timers.delete(id);

  // An animation's virtual start is the clock time at which it first exists.
  function noteNew() {
    for (const anim of document.getAnimations()) {
      if (!firstSeen.has(anim)) firstSeen.set(anim, now);
    }
  }
  const realAnimate = Element.prototype.animate;
  Element.prototype.animate = function (...args) {
    const anim = realAnimate.apply(this, args);
    firstSeen.set(anim, now);
    return anim;
  };

  function runTimers(t) {
    for (let guard = 0; guard < 100000; guard += 1) {
      let nextId = null;
      let next = null;
      for (const [id, timer] of timers) {
        if (timer.at <= t && (next === null || timer.at < next.at)) {
          nextId = id;
          next = timer;
        }
      }
      if (next === null) return;
      now = next.at;
      if (next.every === null) timers.delete(nextId);
      else next.at += next.every;
      if (typeof next.cb === "function") next.cb(...next.args);
      noteNew();
    }
  }

  function syncAnimations(t) {
    let changing = false;
    for (const anim of document.getAnimations()) {
      if (!firstSeen.has(anim)) firstSeen.set(anim, t);
      anim.pause();
      const local = (t - firstSeen.get(anim)) * (anim.playbackRate || 1);
      anim.currentTime = local;
      const end = anim.effect ? anim.effect.getComputedTiming().endTime : 0;
      if (!(local >= end)) changing = true;
    }
    for (const svg of document.querySelectorAll("svg")) {
      if (svg.ownerSVGElement) continue;
      if (svg.querySelector("animate, animateTransform, animateMotion, set")) {
        svg.pauseAnimations();
        svg.setCurrentTime(t / 1000);
        changing = true;
      }
    }
    return changing;
  }

  async function syncMedia(t) {
    let changing = false;
    for (const media of document.querySelectorAll("video, audio")) {
      changing = true;
      media.muted = true;
      media.pause();
      let target = t / 1000;
      if (media.loop && media.duration > 0) target %= media.duration;
      if (Math.abs(media.currentTime - target) > 0.0005) {
        await new Promise((resolve) => {
          media.addEventListener("seeked", resolve, { once: true });
          media.currentTime = target;
        });
      }
    }
    return changing;
  }

  window.__d2v = {
    get now() {
      return now;
    },
    async step(t) {
      noteNew();
      runTimers(t);
      now = t;
      noteNew();
      const callbacks = [...rafQueue.values()];
      rafQueue.clear();
      for (const cb of callbacks) cb(t);
      noteNew();
      let changing = syncAnimations(t);
      if (await syncMedia(t)) changing = true;
      if (rafQueue.size > 0 || timers.size > 0) changing = true;
      return { changing, animations: document.getAnimations().length };
    },
  };
})();
