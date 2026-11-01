/* julia_visage.js v3 — visage minimaliste avec expressions (inspiré de Cozmo).
   Deux yeux (rectangles arrondis + paupières noires) et une bouche. Aucune animation continue :
   minuteurs ponctuels (clignement, regard, expression au repos) et boucle d'animation seulement
   pendant une transition ou quand Julia parle.
   API : mount(el, opts) -> { setState, setLevel, setHue, setColor, lookAt, lookReset,
                              setExpression, clearExpression, expressions, getState }
   États : idle | listening | thinking | speaking (alias veille, ecoute, reflexion, reponse)
   Expressions : voir EXP ci-dessous. opts.auto=false coupe les mouvements spontanés. */
(function () {
  'use strict';

  var STATES = {
    idle: 'idle', veille: 'idle',
    listening: 'listening', ecoute: 'listening', 'écoute': 'listening',
    thinking: 'thinking', reflexion: 'thinking', 'réflexion': 'thinking',
    speaking: 'speaking', reponse: 'speaking', 'réponse': 'speaking'
  };

  // Oeil : [largeur x, hauteur x, décalage x, décalage y, paupière haute côté nez, côté oreille, arche basse]
  // Bouche : [sourire (négatif = moue), ouverture, demi-largeur]
  function X(l, m, r) { return { l: l, r: r || l, m: m }; }
  var EXP = {
    neutral:      X([1, .96, 0, 0, 0, 0, 0],        [.5, 0, 26]),
    happy:        X([1, .92, 0, 2, 0, 0, 26],       [.9, 0, 28]),
    glee:         X([1, .8, 0, 4, 0, 0, 36],        [1, .28, 28]),
    sad:          X([1, .9, 0, 10, 2, 34, 0],       [-.7, 0, 22]),
    sad_up:       X([1, .9, 0, -6, 2, 34, 0],       [-.7, 0, 22]),
    worried:      X([.95, .95, 0, 4, 0, 24, 0],     [-.3, 0, 22]),
    focused:      X([1.06, .85, 0, 0, 26, 26, 0],   [0, 0, 24]),
    annoyed:      X([1.05, .75, 0, 4, 40, 40, 0],   [-.25, 0, 24]),
    surprised:    X([1.12, 1.22, 0, -4, 0, 0, 0],   [0, .55, 14]),
    skeptic:      X([1.05, 1.1, 0, -4, 0, 0, 0],    [.05, 0, 20], [1.05, .78, 0, 4, 34, 34, 0]),
    bored:        X([1, .75, 0, 8, 40, 40, 0],      [-.1, 0, 22]),
    unimpressed:  X([1.05, .65, 0, 6, 34, 34, 0],   [0, 0, 18]),
    sleepy:       X([1, .7, 0, 12, 52, 52, 0],      [.1, 0, 20]),
    suspicious:   X([1.1, .7, 0, 4, 30, 30, 0],     [-.1, 0, 20]),
    squint:       X([1, .32, 0, 0, 0, 0, 0],        [.6, 0, 26]),
    angry:        X([1, .98, 0, 0, 40, 4, 0],       [-.9, 0, 26]),
    furious:      X([1, 1, 0, 0, 56, 6, 0],         [-1, .3, 26]),
    scared:       X([.85, 1.12, 0, -6, 0, 16, 0],   [-.5, .25, 20]),
    awe:          X([1.15, 1.15, 0, -6, 0, 0, 0],   [0, .35, 14]),
    attentive:    X([1.07, 1.1, 0, -2, 0, 0, 0],    [.15, 0, 20])
  };
  var NAMES = ['neutral', 'happy', 'glee', 'sad', 'sad_up', 'worried', 'focused', 'annoyed', 'surprised',
               'skeptic', 'bored', 'unimpressed', 'sleepy', 'suspicious', 'squint', 'angry', 'furious',
               'scared', 'awe'];
  var BASE = { idle: 'neutral', listening: 'attentive', thinking: 'focused', speaking: 'happy' };
  var IDLE_PICK = ['happy', 'happy', 'glee', 'sleepy', 'squint', 'unimpressed', 'skeptic', 'surprised', 'awe'];

  function vec(e) { return e.l.concat(e.r, e.m); }

  var SVG =
    '<svg viewBox="0 0 360 220" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Julia">' +
    [98, 262].map(function (cx, i) {
      return '<g class="e' + i + '"><rect x="' + (cx - 34) + '" y="44" width="68" height="88" rx="22" fill="var(--jc)"/>' +
        '<polygon class="t" points="" fill="#000"/><circle class="b" cx="' + cx + '" cy="400" r="44" fill="#000"/></g>';
    }).join('') +
    '<path class="m" d="" fill="none" stroke="var(--jc)" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>' +
    '</svg>';

  function mount(el, opts) {
    opts = opts || {};
    var auto = opts.auto !== false;
    el.classList.add('jf');
    el.style.setProperty('--jc', '#e0a93b');
    el.style.display = 'block';
    el.innerHTML = SVG;
    var svg = el.querySelector('svg');
    svg.style.cssText = 'display:block;width:100%;height:100%';

    var G = [el.querySelector('.e0'), el.querySelector('.e1')];
    var LID = [G[0].querySelector('.t'), G[1].querySelector('.t')];
    var BOT = [G[0].querySelector('.b'), G[1].querySelector('.b')];
    var MOUTH = el.querySelector('.m');

    var state = 'idle', level = 0, lastLevel = 0;
    var over = null, temp = null;
    var cur = vec(EXP.neutral), tgt = cur.slice(), from = cur.slice(), t0 = 0, dur = 0;
    var gx = 0, gy = 0, gtx = 0, gty = 0, hold = 0;
    var blinkK = 0, mo = 0, nextSyl = 0, sylT = 0;
    var raf = 0, tBlink = 0, tIdle = 0, tGaze = 0, tThink = 0, tSwap = 0;

    function eye(i, o, cx) {
      var v = cur;
      var sx = v[o], sy = v[o + 1] * (1 - blinkK * .93) + (state === 'listening' ? level * .22 : 0);
      var dx = v[o + 2] + gx, dy = v[o + 3] + gy, ti = v[o + 4], to = v[o + 5], bl = v[o + 6];
      G[i].setAttribute('transform', 'translate(' + (cx + dx).toFixed(1) + ' ' + (88 + dy).toFixed(1) +
        ') scale(' + sx.toFixed(3) + ' ' + sy.toFixed(3) + ') translate(' + (-cx) + ' -88)');
      var x0 = cx - 44, x1 = cx + 44, y0 = 34;
      var l = i === 0 ? to : ti, r = i === 0 ? ti : to;      // i=0 : oeil gauche (nez à droite)
      LID[i].setAttribute('points', x0 + ',' + y0 + ' ' + x1 + ',' + y0 + ' ' + x1 + ',' + (44 + r).toFixed(1) +
        ' ' + x0 + ',' + (44 + l).toFixed(1));
      BOT[i].setAttribute('cy', bl > .5 ? (132 - bl + 44).toFixed(1) : 400);
    }

    function render() {
      eye(0, 0, 98);
      eye(1, 7, 262);
      var smile = cur[14], op = Math.max(cur[15], mo), w = cur[16] - op * 6;
      var cy = 178 - smile * 9, ty = 178 + smile * 6, by = ty + 1 + op * 44;
      MOUTH.setAttribute('d', 'M' + (180 - w).toFixed(1) + ' ' + cy.toFixed(1) + 'Q180 ' + ty.toFixed(1) + ' ' +
        (180 + w).toFixed(1) + ' ' + cy.toFixed(1) + 'Q180 ' + by.toFixed(1) + ' ' + (180 - w).toFixed(1) + ' ' + cy.toFixed(1) + 'Z');
      MOUTH.setAttribute('fill', op > .12 ? 'var(--jc)' : 'none');
    }

    function kick() { if (!raf) raf = requestAnimationFrame(frame); }

    function frame(t) {
      raf = 0;
      var moving = false, i;
      if (dur > 0) {
        var k = Math.min(1, (t - t0) / dur), e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
        for (i = 0; i < cur.length; i++) cur[i] = from[i] + (tgt[i] - from[i]) * e;
        if (k < 1) moving = true; else dur = 0;
      }
      if (Math.abs(gtx - gx) > .2 || Math.abs(gty - gy) > .2) { gx += (gtx - gx) * .35; gy += (gty - gy) * .35; moving = true; }
      else { gx = gtx; gy = gty; }
      if (state === 'speaking') {
        var target;
        if (t - lastLevel < 300) target = Math.min(1, level * 1.4);
        else {
          if (t > nextSyl) { sylT = Math.random() < .18 ? .04 : .25 + Math.random() * .65; nextSyl = t + 80 + Math.random() * 110; }
          target = sylT;
        }
        mo += (target - mo) * .4;
        moving = true;
      } else if (mo > .01) { mo *= .6; moving = true; } else mo = 0;
      render();
      if (moving) raf = requestAnimationFrame(frame);
    }

    function go(name, ms) {
      var e = EXP[name] || EXP.neutral;
      from = cur.slice(); tgt = vec(e); t0 = performance.now(); dur = ms == null ? 260 : ms;
      if (dur <= 0) { cur = tgt.slice(); dur = 0; render(); }
      kick();
    }
    function applyBase(ms) { go(over || temp || BASE[state], ms); }

    // ---- mouvements spontanés (minuteurs ponctuels : rien ne tourne entre deux) ----
    function rnd(a, b) { return a + Math.random() * (b - a); }
    function scheduleBlink() {
      clearTimeout(tBlink);
      if (!auto || document.hidden) return;
      tBlink = setTimeout(function () {
        blinkK = .6; render();
        tBlink = setTimeout(function () { blinkK = 1; render();
          tBlink = setTimeout(function () { blinkK = .5; render();
            tBlink = setTimeout(function () { blinkK = 0; render(); scheduleBlink(); }, 45); }, 70); }, 40);
      }, rnd(2500, 7000));
    }
    function scheduleIdle() {
      clearTimeout(tIdle);
      if (!auto || document.hidden) return;
      tIdle = setTimeout(function () {
        if (state === 'idle' && !over && !hold) {
          temp = IDLE_PICK[Math.floor(Math.random() * IDLE_PICK.length)];
          applyBase(280);
          tIdle = setTimeout(function () { temp = null; applyBase(320); scheduleIdle(); }, rnd(2200, 3600));
        } else scheduleIdle();
      }, rnd(7000, 15000));
    }
    function scheduleGaze() {
      clearTimeout(tGaze);
      if (!auto || document.hidden) return;
      tGaze = setTimeout(function () {
        if (state === 'idle' && !hold) { gtx = rnd(-9, 9); gty = rnd(-4, 4); kick();
          setTimeout(function () { if (!hold) { gtx = 0; gty = 0; kick(); } }, rnd(900, 1800)); }
        scheduleGaze();
      }, rnd(3500, 7000));
    }
    function stopThink() { clearInterval(tThink); tThink = 0; }
    function startThink() {
      stopThink();
      var xs = [-16, 0, 16, 8, -8];
      tThink = setInterval(function () {
        if (!hold) { gtx = xs[Math.floor(Math.random() * xs.length)]; gty = Math.round(rnd(-6, 2)); kick(); }
      }, 1000);
    }
    function stopSwap() { clearInterval(tSwap); tSwap = 0; }
    function startSwap() {
      stopSwap();
      var g = false;
      tSwap = setInterval(function () {
        if (state === 'speaking' && !over) { g = !g; temp = g ? 'glee' : null; applyBase(300); }
      }, 2600);
    }
    function startAll() { scheduleBlink(); scheduleIdle(); scheduleGaze(); }
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) { clearTimeout(tBlink); clearTimeout(tIdle); clearTimeout(tGaze); stopThink(); stopSwap(); }
      else { startAll(); if (state === 'thinking') startThink(); if (state === 'speaking') startSwap(); }
    });

    var api = {
      setState: function (s) {
        s = STATES[s] || 'idle';
        state = s; el.dataset.state = s;
        temp = null;
        if (s !== 'speaking' && s !== 'listening') api.setLevel(0);
        stopThink(); stopSwap();
        if (s === 'thinking') startThink(); else if (!hold) { gtx = 0; gty = 0; }
        if (s === 'speaking') startSwap();
        applyBase(260);
      },
      setLevel: function (v) {
        level = Math.max(0, Math.min(1, +v || 0));
        lastLevel = performance.now();
        el.style.setProperty('--lvl', level.toFixed(2));
        kick();
      },
      setHue: function (h) { el.style.setProperty('--jc', 'hsl(' + h + ' 92% 58%)'); },
      setColor: function (c) { el.style.setProperty('--jc', c); },
      setExpression: function (name, ms) { if (EXP[name]) { over = name; go(name, ms); } },
      clearExpression: function () { over = null; applyBase(260); },
      expressions: function () { return NAMES.slice(); },
      lookAt: function (x, y) {
        var r = el.getBoundingClientRect();
        hold = 1;
        gtx = Math.max(-1, Math.min(1, ((x - r.left) / r.width - .5) * 2)) * 18;
        gty = Math.max(-1, Math.min(1, ((y - r.top) / r.height - .5) * 2)) * 10;
        kick();
      },
      lookReset: function () { hold = 0; gtx = 0; gty = 0; kick(); },
      getState: function () { return state; }
    };

    render();
    api.setState('idle');
    if (auto) startAll();
    return api;
  }

  window.JuliaFace = { mount: mount, expressions: NAMES.slice() };
})();
