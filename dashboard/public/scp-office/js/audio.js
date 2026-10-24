/**
 * audio.js — Bộ tổng hợp âm thanh 8-bit (Web Audio API, không phụ thuộc ngoài).
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.ScpAudio = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function createAudio() {
    let ctx = null;
    const state = { enabled: false };

    function playBeep(freq, duration, type) {
      if (!state.enabled) return;
      try {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) return;
        if (!ctx) ctx = new AudioContextClass();
        if (ctx.state === "suspended") ctx.resume();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = type || "square";
        osc.frequency.setValueAtTime(freq || 520, ctx.currentTime);
        gain.gain.setValueAtTime(0.035, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + (duration || 0.06));
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + (duration || 0.06));
      } catch (e) {
        /* im lặng: âm thanh là tiện ích, không được làm hỏng ứng dụng */
      }
    }

    function toggle() {
      state.enabled = !state.enabled;
      if (state.enabled) playBeep(680, 0.08);
      return state.enabled;
    }

    return { state, playBeep, toggle };
  }

  return { createAudio };
});
