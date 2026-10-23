/* ForLife "blue door": fake-door test. No dependencies. Load after the markup.
   Tappable elements carry data-door="open" | "vera" | "specialist".
   Every count goes through one hook the page defines:
     window.blueDoorEvent(name, detail)
       name:   "open" | "vera" | "specialist" | "close"
       detail: {} for open/vera/specialist;
               for close: { via: "escape" | "backdrop" | "button", after: "vera" | "specialist" | null } */
(() => {
  const pane = document.querySelector('.bd-pane[data-door="open"]');
  const dlg = document.getElementById('bd-dialog');
  if (!pane || !dlg) return;
  if (typeof dlg.showModal !== 'function') { pane.hidden = true; return; }   // very old browser: no door at all

  const $ = (s) => dlg.querySelector(s);
  const title = $('#bd-title'), choose = $('.bd-choose'), soon = $('.bd-soon'), soonTitle = $('#bd-soon-title');
  const soonImg = $('.bd-soon img'), status = $('.bd-status'), actions = $('.bd-actions');
  const calm = matchMedia('(prefers-reduced-motion: reduce)');
  let chosen = null, from = null, via = 'button', downOnBackdrop = false, timer = 0;

  const log = (name, detail = {}) => {
    try { if (typeof window.blueDoorEvent === 'function') window.blueDoorEvent(name, detail); } catch (e) { /* counting must never break the page */ }
  };

  // Tablet and desktop: hang the panel from the pane. Phones get a bottom sheet (CSS ignores these).
  const place = () => {
    const r = pane.getBoundingClientRect();
    dlg.style.setProperty('--bd-top', Math.round(r.bottom + 10) + 'px');
    dlg.style.setProperty('--bd-right', Math.max(10, Math.round(innerWidth - r.right)) + 'px');
  };

  const showChoice = () => {
    choose.hidden = false; title.hidden = false;
    soon.hidden = true; actions.hidden = true;
    status.textContent = '';
  };

  const showSoon = (which, btn) => {
    const img = btn.querySelector('img');
    soonImg.src = img.currentSrc || img.src;
    soon.dataset.door = which;
    choose.hidden = true; title.hidden = true;
    soon.hidden = false; actions.hidden = false;
    soonTitle.focus();
    // fill the live region a moment after it is shown, so it is announced
    clearTimeout(timer);
    timer = setTimeout(() => status.replaceChildren(document.getElementById('bd-copy-' + which).content.cloneNode(true)), 60);
  };

  const open = () => {
    if (dlg.open) return;
    chosen = null; via = 'button';
    showChoice(); place();
    document.documentElement.classList.add('bd-lock');
    dlg.classList.remove('bd-closing');
    dlg.showModal();
    title.focus();   // a neutral start: neither door gets focus first
    log('open');
  };

  const close = (how) => {
    if (!dlg.open || dlg.classList.contains('bd-closing')) return;
    via = how;
    if (calm.matches) { dlg.close(); return; }
    dlg.classList.add('bd-closing');
    setTimeout(() => dlg.close(), 160);
  };

  pane.addEventListener('click', open);

  dlg.addEventListener('click', (e) => {
    const t = e.target;
    if (t === dlg) { if (downOnBackdrop) close('backdrop'); return; }
    const door = t.closest('[data-door]');
    if (door) { chosen = door.dataset.door; from = door; log(chosen); showSoon(chosen, door); return; }
    if (t.closest('[data-bd-back]')) { showChoice(); (from || choose.querySelector('[data-door]')).focus(); return; }
    if (t.closest('[data-bd-close]')) close('button');
  });
  dlg.addEventListener('pointerdown', (e) => { downOnBackdrop = e.target === dlg; });

  // Escape: close with the same exit as every other way out
  dlg.addEventListener('cancel', (e) => { e.preventDefault(); close('escape'); });

  // One place for "close", whichever way the dialog was closed
  dlg.addEventListener('close', () => {
    clearTimeout(timer);
    dlg.classList.remove('bd-closing');
    document.documentElement.classList.remove('bd-lock');
    log('close', { via, after: chosen });
    pane.focus();
  });

  // Keep Tab inside the dialog
  dlg.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const items = [...dlg.querySelectorAll('button')].filter((b) => b.offsetParent !== null && !b.disabled);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1], at = document.activeElement;
    if (e.shiftKey && (at === first || at === title || !dlg.contains(at))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (at === last || !dlg.contains(at))) { e.preventDefault(); first.focus(); }
  });

  addEventListener('resize', () => { if (dlg.open) place(); });
})();
