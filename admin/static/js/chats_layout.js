/* global document, window */
(function () {
  const layout = document.getElementById('chats_layout');
  if (!layout) return;

  const buttons = layout.querySelectorAll('.chats_ctrl[data-open]');
  const leftEdge  = layout.querySelector('.chats_edge--left');
  const rightEdge = layout.querySelector('.chats_edge--right');

  const isMobile  = () => window.matchMedia('(max-width: 550px)').matches;
  const isTablet  = () => window.matchMedia('(min-width: 551px) and (max-width: 1024px)').matches;
  const isDesktop = () => window.matchMedia('(min-width: 1025px)').matches;

  const setActive = (target) => {
    const allowed = ['main', 's1', 's2'];
    if (!allowed.includes(target)) return;

    // на планшете main не показываем — принудительно заменяем на s1
    if (isTablet() && target === 'main') {
      target = 's2';
    }

    layout.setAttribute('data-active', target);

    // фокус в активную панель
    const activeId = target === 'main' ? 'chats_main' : (target === 's1' ? 'chats_sidebar_1' : 'chats_sidebar_2');
    const el = document.getElementById(activeId);
    if (el) el.focus({ preventScroll: true });
  };

  // кнопки
  buttons.forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      setActive(btn.getAttribute('data-open'));
    });
  });

  // ------- swipe logic (mobile, цепочка s1 <-> s2 <-> main) -------
  let touchStartX = 0;
  let touchStartY = 0;
  const EDGE_ACTIVATION_WIDTH = 24; // px

  const beganOnLeftEdge  = (x) => x <= EDGE_ACTIVATION_WIDTH;
  const beganOnRightEdge = (x) => (window.innerWidth - x) <= EDGE_ACTIVATION_WIDTH;

  document.addEventListener('touchstart', (ev) => {
    if (!isMobile()) return;
    const t = ev.touches[0];
    touchStartX = t.clientX;
    touchStartY = t.clientY;
  }, { passive: true });

  document.addEventListener('touchend', (ev) => {
    if (!isMobile()) return;

    const t = ev.changedTouches[0];
    const dx = t.clientX - touchStartX;
    const dy = t.clientY - touchStartY;
    if (Math.abs(dx) < 40 || Math.abs(dx) < Math.abs(dy)) return;

    const active = layout.getAttribute('data-active');

    if (dx > 0) {
      // свайп вправо (из левого края)
      if (beganOnLeftEdge(touchStartX)) {
        if (active === 'main') setActive('s2');
        else if (active === 's2') setActive('s1');
        // s1 -> дальше некуда
      }
    } else {
      // свайп влево (с правого края)
      if (beganOnRightEdge(touchStartX)) {
        if (active === 's1') setActive('s2');
        else if (active === 's2') setActive('main');
        // main -> дальше некуда
      }
    }
  }, { passive: true });

  // edge click fallback (мышью на мобильном)
  if (leftEdge)  leftEdge.addEventListener('click', () => {
    if (!isMobile()) return;
    const active = layout.getAttribute('data-active');
    if (active === 'main') setActive('s2');
    else if (active === 's2') setActive('s1');
  });

  if (rightEdge) rightEdge.addEventListener('click', () => {
    if (!isMobile()) return;
    const active = layout.getAttribute('data-active');
    if (active === 's1') setActive('s2');
    else if (active === 's2') setActive('main');
  });

  // Подгоняем состояние при ресайзе
  const applyViewportRules = () => {
    if (isTablet()) {
      const a = layout.getAttribute('data-active');
      if (a === 'main') setActive('s2');
    }
  };
  window.addEventListener('resize', applyViewportRules);
  applyViewportRules(); // на загрузке
  if (isMobile() || isTablet()) setActive('s2');
})();
