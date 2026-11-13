'use strict';
// Shared keyboard navigation for the workbench and automatic search webviews.
window.PS2DECOMP_TABS = function (list, onChange = () => {}) {
  const buttons = [...list.querySelectorAll('[data-tab]')];
  list.setAttribute('role', 'tablist');
  for (const button of buttons) {
    const pane = document.getElementById(button.dataset.tab);
    button.id ||= button.dataset.tab + '-tab';
    button.setAttribute('role', 'tab');
    button.setAttribute('aria-controls', pane.id);
    pane.setAttribute('role', 'tabpanel');
    pane.setAttribute('aria-labelledby', button.id);
    pane.tabIndex = 0;
    button.addEventListener('click', () => {activate(pane.id); onChange(pane.id);});
    button.addEventListener('keydown', event => {
      const index = buttons.indexOf(button);
      const next = {ArrowRight: (index + 1) % buttons.length, ArrowLeft: (index - 1 + buttons.length) % buttons.length,
        Home: 0, End: buttons.length - 1}[event.key];
      if (next === undefined) return;
      event.preventDefault();
      activate(buttons[next].dataset.tab, true); onChange(buttons[next].dataset.tab);
    });
  }
  function activate(id, focus = false) {
    if (!buttons.some(button => button.dataset.tab === id)) return;
    for (const button of buttons) {
      const active = button.dataset.tab === id, pane = document.getElementById(button.dataset.tab);
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
      pane.classList.toggle('active', active); pane.hidden = !active;
      if (active && focus) button.focus();
    }
  }
  activate((buttons.find(button => button.classList.contains('active')) || buttons[0]).dataset.tab);
  return activate;
};
