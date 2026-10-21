/* One demonstration entry; route changes reuse the existing live workspaces. */
(() => {
  'use strict';

  function init() {
    const workspace = window.WujiWorkspace;
    const directory = document.querySelector('.directory');
    const singlePage = document.getElementById('page-library');
    const singleLink = directory?.querySelector('[data-page="library"]');
    if (!workspace || !singlePage || !singleLink || document.getElementById('demonstration-navigation')) return;

    const multiPage = document.getElementById('page-devices');
    const multiLink = directory.querySelector('[data-page="devices"]');
    const group = document.createElement('div');
    group.id = 'demonstration-navigation';
    group.className = 'hub-tabs demonstration-navigation';
    group.setAttribute('role', 'tablist');
    group.setAttribute('aria-orientation', 'horizontal');
    const options = [
      { route: 'library', id: 'demonstration-single', zh: '单手', en: 'Single hand', page: singlePage },
      { route: 'devices', id: 'demonstration-multi', zh: '多手', en: 'Multiple hands', page: multiPage },
    ];
    let pendingFocus = null;
    const text = (zh, en) => window.WujiLocale?.lang === 'en' ? en : zh;
    const setText = (element, value) => {
      if (element && element.textContent !== value) element.textContent = value;
    };
    const activeRoute = () => document.body.dataset.page || location.hash.slice(1);

    function select(option, focus) {
      if (!option.page) return;
      if (activeRoute() === option.route) {
        if (focus) option.button.focus({ preventScroll: true });
        return;
      }
      pendingFocus = focus ? option.button : null;
      // The existing hash router owns viewer placement and page lifecycle.
      // A choice never connects a device or starts/stops an action.
      location.hash = option.route;
    }

    for (const [index, option] of options.entries()) {
      const button = document.createElement('button');
      option.button = button;
      button.type = 'button';
      button.id = option.id;
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-controls', option.page?.id || 'page-devices');
      button.disabled = !option.page;
      button.addEventListener('click', () => select(option, document.activeElement === button));
      button.addEventListener('keydown', event => {
        const available = options.filter(item => item.page);
        const position = available.indexOf(option);
        let next;
        if (event.key === 'ArrowRight') next = available[(position + 1) % available.length];
        else if (event.key === 'ArrowLeft') next = available[(position + available.length - 1) % available.length];
        else if (event.key === 'Home') next = available[0];
        else if (event.key === 'End') next = available[available.length - 1];
        if (!next) return;
        event.preventDefault();
        select(next, true);
      });
      button.tabIndex = index === 0 ? 0 : -1;
      group.append(button);
    }

    // Keep the legacy link in the DOM: the original router still addresses it.
    if (multiLink) {
      multiLink.dataset.demonstrationHidden = 'true';
      multiLink.hidden = true;
      multiLink.setAttribute('aria-hidden', 'true');
      multiLink.tabIndex = -1;
    }

    function labels() {
      const title = text('灵巧手展示', 'Hand demonstrations');
      group.setAttribute('aria-label', title);
      const linkLabel = singleLink.querySelector('span');
      linkLabel?.removeAttribute('data-i18n');
      setText(linkLabel, title);
      for (const option of options) {
        setText(option.button, text(option.zh, option.en));
        const heading = option.page?.querySelector(':scope > .page-heading h2');
        heading?.removeAttribute('data-i18n');
        setText(heading, title);
      }
      const multiHeading = multiPage?.querySelector('#wsx-title');
      // This title is now owned by the shared navigation, not the old page label.
      multiHeading?.removeAttribute('data-wsx-t');
      multiHeading?.removeAttribute('data-i18n');
      setText(multiHeading, title);
      if (['library', 'devices'].includes(activeRoute())) document.title = title + ' · Hand Workbench';
    }

    function sync() {
      const route = activeRoute();
      const selected = route === 'devices' ? 'devices' : 'library';
      for (const option of options) {
        const active = option.route === selected;
        option.button.setAttribute('aria-selected', String(active));
        option.button.tabIndex = active ? 0 : -1;
      }
      if (route === 'library' || route === 'devices') {
        // Move only this one group. Existing headers, controls, frames and state
        // remain untouched, including the page-specific header actions.
        const heading = route === 'devices'
          ? multiPage?.querySelector('.wsx-head') || multiPage?.querySelector(':scope > .page-heading')
          : singlePage.querySelector(':scope > .page-heading');
        if (heading && heading.nextElementSibling !== group) heading.after(group);
        singleLink.setAttribute('aria-current', 'page');
        multiLink?.removeAttribute('aria-current');
      }
      labels();
      if (pendingFocus && !group.hidden) {
        const button = pendingFocus;
        pendingFocus = null;
        button.focus({ preventScroll: true });
      }
    }

    window.addEventListener('workspace-page', sync);
    window.addEventListener('wuji-language', sync);
    window.addEventListener('experience-mode', sync);
    // Mount while another page is active too, so its DOM identity is stable.
    singlePage.querySelector(':scope > .page-heading')?.after(group);
    sync();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
