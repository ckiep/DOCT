/* VERNY Bakery — page behaviour: mobile menu, full-menu dialog, photo viewer,
   pre-order form → WhatsApp, KZ / RU / EN switch. No dependencies. */
(() => {
  'use strict';

  // ---- business settings: change here (and the same values in index.html)
  const WA_NUMBER = '77005289504';         // WhatsApp: digits only, with country code
  const PHONE_LABEL = '+7 700 528 95 04';  // phone as shown to people
  const OPEN = '07:30', CLOSE = '22:00';   // pickup window = opening hours

  const { MSG, DICT } = window.VERNY_I18N;
  const VARS = { phone: PHONE_LABEL, open: OPEN, close: CLOSE };
  let lang = 'ru';
  const t = key => MSG[lang][key].replace(/\{(\w+)\}/g, (_, k) => VARS[k]);

  // ---- mobile menu
  const header = document.querySelector('header');
  const burger = document.getElementById('burger');
  const menu = document.getElementById('menu');
  const setMenu = open => { menu.classList.toggle('open', open); burger.setAttribute('aria-expanded', String(open)); };
  burger.addEventListener('click', () => setMenu(!menu.classList.contains('open')));
  menu.querySelectorAll('a').forEach(a => a.addEventListener('click', () => setMenu(false)));
  document.addEventListener('click', e => { if (menu.classList.contains('open') && !header.contains(e.target)) setMenu(false); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && menu.classList.contains('open')) { setMenu(false); burger.focus(); }
  });

  // ---- dialogs (full menu, photo viewer)
  const openDialog = d => { if (typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', ''); };
  const closeDialog = d => { if (typeof d.close === 'function') d.close(); else d.removeAttribute('open'); };
  document.querySelectorAll('dialog').forEach(d => {
    d.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => closeDialog(d)));
    d.addEventListener('click', e => { if (e.target === d) closeDialog(d); });   // click on the backdrop
  });
  const fullMenu = document.getElementById('fullmenu');
  document.querySelectorAll('[data-open-menu]').forEach(b => b.addEventListener('click', () => openDialog(fullMenu)));

  // gallery: every photo opens in the viewer, by mouse, touch or keyboard
  const viewer = document.getElementById('viewer');
  const vImg = viewer.querySelector('img');
  const vCap = viewer.querySelector('figcaption');
  viewer.addEventListener('click', () => closeDialog(viewer));
  viewer.addEventListener('close', () => { vImg.src = 'data:,'; vImg.alt = ''; vCap.textContent = ''; });
  document.querySelectorAll('.gallery figure').forEach(f => {
    const img = f.querySelector('img');
    f.tabIndex = 0;
    f.setAttribute('role', 'button');
    const show = () => { vImg.src = img.currentSrc || img.src; vImg.alt = img.alt; vCap.textContent = img.alt; openDialog(viewer); };
    f.addEventListener('click', show);
    f.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); show(); } });
  });

  // ---- pre-order form → WhatsApp message
  const pad = n => String(n).padStart(2, '0');
  const ymd = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;  // local date, not UTC
  const hm = d => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

  const form = document.getElementById('pre-form');
  const msg = document.getElementById('form-msg');
  const dateIn = form.elements.date, timeIn = form.elements.time;
  const now = new Date(), tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  dateIn.min = ymd(now);
  dateIn.value = ymd(tomorrow);
  timeIn.min = OPEN;
  timeIn.max = CLOSE;

  const say = (text, isError) => { msg.textContent = text; msg.classList.toggle('err', Boolean(isError)); };
  const fail = (key, field) => { say(t(key), true); field.setAttribute('aria-invalid', 'true'); field.focus(); };
  form.addEventListener('input', e => e.target.removeAttribute('aria-invalid'));

  form.addEventListener('submit', e => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(form));
    const name = (f.name || '').trim(), phone = (f.phone || '').trim();
    const today = ymd(new Date());
    if (!name) return fail('needName', form.elements.name);
    if (phone.replace(/\D/g, '').length < 10) return fail('badPhone', form.elements.phone);
    if (!f.date || f.date < today) return fail('badDate', dateIn);
    if (!f.time || f.time < OPEN || f.time > CLOSE) return fail('badTime', timeIn);
    if (f.date === today && f.time <= hm(new Date())) return fail('pastTime', timeIn);

    const [y, m, d] = f.date.split('-');
    const text = [
      t('hello'),
      `${t('name')}: ${name}`,
      `${t('phone')}: ${phone}`,
      `${t('date')}: ${d}.${m}.${y}`,
      `${t('time')}: ${f.time}`,
      `${t('order')}: ${(f.items || '').trim() || '—'}`,
    ].join('\n');
    const a = document.createElement('a');
    a.href = `https://wa.me/${WA_NUMBER}?text=${encodeURIComponent(text)}`;
    a.target = '_blank';
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
    say(t('sent'));
  });

  // ---- languages: KZ / RU / EN. Russian is the source text in the markup.
  const IDX = { kz: 0, en: 1 };
  const ATTRS = ['alt', 'placeholder', 'aria-label', 'title'];
  const textNodes = [], attrNodes = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = walker.nextNode());) {
    if (n.parentElement.closest('script,style')) continue;
    const key = n.nodeValue.replace(/\s+/g, ' ').trim();
    if (DICT[key]) textNodes.push({ n, key, lead: n.nodeValue.match(/^\s*/)[0], trail: n.nodeValue.match(/\s*$/)[0] });
  }
  document.querySelectorAll(ATTRS.map(a => `[${a}]`).join(',')).forEach(el => ATTRS.forEach(a => {
    const v = el.getAttribute(a);
    if (v && DICT[v]) attrNodes.push({ el, a, key: v });
  }));
  const tr = key => (lang === 'ru' ? key : DICT[key][IDX[lang]]);

  function setLang(l) {
    lang = l;
    textNodes.forEach(x => { x.n.nodeValue = x.lead + tr(x.key) + x.trail; });
    attrNodes.forEach(x => x.el.setAttribute(x.a, tr(x.key)));
    document.documentElement.lang = l === 'kz' ? 'kk' : l;
    document.title = t('title');
    document.querySelectorAll('[data-lang]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.lang === l)));
    say('');
    form.querySelectorAll('[aria-invalid]').forEach(el => el.removeAttribute('aria-invalid'));
    try { localStorage.setItem('verny-lang', l); } catch (e) { /* private mode */ }
  }
  document.querySelectorAll('[data-lang]').forEach(b => b.addEventListener('click', () => setLang(b.dataset.lang)));

  const LANGS = ['kz', 'ru', 'en'];
  let start = 'ru';
  const fromHash = location.hash.slice(1);
  if (LANGS.includes(fromHash)) start = fromHash;
  else { try { const s = localStorage.getItem('verny-lang'); if (LANGS.includes(s)) start = s; } catch (e) { /* private mode */ } }
  if (start !== 'ru') setLang(start);

  // ---- gentle reveal (content is visible at rest)
  const reveals = document.querySelectorAll('.reveal');
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(es => es.forEach(x => {
      if (x.isIntersecting) { x.target.classList.add('in'); io.unobserve(x.target); }
    }), { threshold: 0.12 });
    reveals.forEach(el => io.observe(el));
  } else reveals.forEach(el => el.classList.add('in'));
})();
