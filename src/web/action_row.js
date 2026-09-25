/*
 * WorkbenchActionRow: generic action row for a bilingual desktop settings UI.
 *
 *   const row = window.WorkbenchActionRow.create({
 *     kind: 'link',            // 'button' (default) or 'link'
 *     label: 'Glove',          // required, visible, non-empty
 *     detail: 'Right hand',    // optional secondary text
 *     icon: 'glove',           // connection|glove|calibration|mapping|settings|external
 *     href: '#glove',          // links only: '#fragment' or absolute https:// URL
 *     onActivate: function (clickEvent) {}  // optional; a link's default is then prevented
 *   });
 *   container.appendChild(row.element);
 *   row.setLabels({ label: translatedLabel, detail: translatedDetail });
 *   row.setDisabled(true);
 *   row.destroy();
 *
 * Notes:
 * - Malformed options throw a TypeError synchronously, before any DOM is
 *   created. This covers wrong types, an empty label, an unknown kind, icon
 *   or key, and a bad href.
 * - setLabels() replaces both strings. Omit detail (or pass '') to remove it.
 * - The trailing indicator is an outward arrow when the row leaves the page
 *   (icon 'external' or an https:// link). Otherwise it is a chevron.
 * - After destroy(), setLabels() and setDisabled() still validate their input
 *   but do nothing.
 * - No networking, storage, timers, HTML strings, eval or telemetry. Styling
 *   is left to the host. All classes and ids use the "wa-" prefix.
 */
(function (global) {
  'use strict';

  // If the file is loaded twice, keep the first installation (and its id counter).
  if (global.WorkbenchActionRow && typeof global.WorkbenchActionRow.create === 'function') {
    return;
  }

  const SVG_NS = 'http://www.w3.org/2000/svg';
  const KINDS = ['button', 'link'];
  const CREATE_KEYS = ['kind', 'label', 'detail', 'icon', 'href', 'onActivate'];
  const LABEL_KEYS = ['label', 'detail'];
  // These characters render as nothing. Text made only of them counts as empty.
  const INVISIBLE_CHARS = /[\u00AD\u200B-\u200F\u2060\uFEFF]/g;
  // Whitespace, control characters and backslashes are never accepted in href.
  const UNSAFE_HREF_CHARS = /[\s\u0000-\u001F\u007F\\]/;

  let idCounter = 0;

  // Outline glyphs on a 20x20 grid. createIcon() applies size, stroke and
  // accessibility attributes to all of them in the same way.
  const ICONS = {
    // Two joined chain links.
    connection: [
      ['path', { d: 'M7.5 12.5l5-5' }],
      ['path', { d: 'M9.5 6l1.75-1.75a3.18 3.18 0 0 1 4.5 4.5L14 10.5' }],
      ['path', { d: 'M10.5 14l-1.75 1.75a3.18 3.18 0 0 1-4.5-4.5L6 9.5' }]
    ],
    // Glove: four fingers, thumb and cuff seam.
    glove: [
      ['path', {
        d: 'M7.5 17.5V15L6.5 13.75L3.3 10.6A1.25 1.25 0 0 1 5.1 8.8L6.5 10.2' +
          'V5.5A1.25 1.25 0 0 1 9 5.5V4.25A1.25 1.25 0 0 1 11.5 4.25' +
          'V4.75A1.25 1.25 0 0 1 14 4.75V6.5A1.25 1.25 0 0 1 16.5 6.5' +
          'V13L15.5 15V17.5Z'
      }],
      ['path', { d: 'M9 5.5V9.5M11.5 4.75V9.5M14 6.5V9.5M7.5 15H15.5' }]
    ],
    // Crosshair target.
    calibration: [
      ['circle', { cx: '10', cy: '10', r: '5.5' }],
      ['circle', { cx: '10', cy: '10', r: '1.5' }],
      ['path', { d: 'M10 1.75V4.5M10 15.5V18.25M1.75 10H4.5M15.5 10H18.25' }]
    ],
    // Source node routed to a target node.
    mapping: [
      ['circle', { cx: '5', cy: '5.5', r: '2' }],
      ['circle', { cx: '15', cy: '14.5', r: '2' }],
      ['path', { d: 'M7 5.5H12A2.25 2.25 0 0 1 12 10H8A2.25 2.25 0 0 0 8 14.5H13' }]
    ],
    // Eight-tooth gear.
    settings: [
      ['path', {
        d: 'M15.87 8.75H18V11.25H15.87A6 6 0 0 1 15.03 13.27' +
          'L16.54 14.77L14.77 16.54L13.27 15.03A6 6 0 0 1 11.25 15.87' +
          'V18H8.75V15.87A6 6 0 0 1 6.73 15.03' +
          'L5.23 16.54L3.46 14.77L4.97 13.27A6 6 0 0 1 4.13 11.25' +
          'H2V8.75H4.13A6 6 0 0 1 4.97 6.73' +
          'L3.46 5.23L5.23 3.46L6.73 4.97A6 6 0 0 1 8.75 4.13' +
          'V2H11.25V4.13A6 6 0 0 1 13.27 4.97' +
          'L14.77 3.46L16.54 5.23L15.03 6.73A6 6 0 0 1 15.87 8.75Z'
      }],
      ['circle', { cx: '10', cy: '10', r: '2.5' }]
    ],
    // Box with an outward arrow.
    external: [
      ['path', {
        d: 'M9 4.5H5.5A1.5 1.5 0 0 0 4 6V14.5A1.5 1.5 0 0 0 5.5 16' +
          'H14A1.5 1.5 0 0 0 15.5 14.5V11'
      }],
      ['path', { d: 'M12 4H16V8M16 4L9 11' }]
    ]
  };

  // Trailing indicators: the chevron means "more inside the app", the arrow
  // means "leaves the page".
  const TRAIL_ICONS = {
    chevron: [
      ['path', { d: 'M8 5L13 10L8 15' }]
    ],
    external: [
      ['path', { d: 'M6.5 13.5L13.5 6.5M8 6.5H13.5V12' }]
    ]
  };

  function fail(message) {
    throw new TypeError('WorkbenchActionRow: ' + message);
  }

  function isAbsent(value) {
    return value === undefined || value === null;
  }

  function isPlainObject(value) {
    return value !== null &&
      typeof value === 'object' &&
      Object.prototype.toString.call(value) === '[object Object]';
  }

  function hasOwn(object, key) {
    return Object.prototype.hasOwnProperty.call(object, key);
  }

  function assertKnownKeys(object, allowed, where) {
    Object.keys(object).forEach(function (key) {
      if (allowed.indexOf(key) === -1) {
        fail(where + ': unknown option "' + key + '"');
      }
    });
  }

  function isBlank(text) {
    return text.replace(INVISIBLE_CHARS, '').trim() === '';
  }

  function readLabel(value, where) {
    // Every row needs visible text. That text is also the accessible name,
    // so icon-only rows cannot be created.
    if (typeof value !== 'string' || isBlank(value)) {
      fail(where + ': label must be a non-empty string');
    }
    return value;
  }

  function readDetail(value, where) {
    if (isAbsent(value)) {
      return '';
    }
    if (typeof value !== 'string') {
      fail(where + ': detail must be a string when provided');
    }
    return isBlank(value) ? '' : value;
  }

  function isSamePageHash(href) {
    // A bare '#' is a placeholder, not a destination, so it is rejected.
    return href.length > 1 && href.charAt(0) === '#' && !UNSAFE_HREF_CHARS.test(href);
  }

  function isHttpsUrl(href) {
    if (!/^https:\/\//i.test(href) || UNSAFE_HREF_CHARS.test(href)) {
      return false;
    }
    let url;
    try {
      url = new URL(href);
    } catch (error) {
      return false;
    }
    // Credentials in the URL (https://user@host) can disguise the destination.
    return url.protocol === 'https:' && url.username === '' && url.password === '';
  }

  function readOptions(options) {
    if (!isPlainObject(options)) {
      fail('create(options) expects a plain object');
    }
    assertKnownKeys(options, CREATE_KEYS, 'create');

    const kind = isAbsent(options.kind) ? 'button' : options.kind;
    if (KINDS.indexOf(kind) === -1) {
      fail('create: kind must be "button" or "link"');
    }

    const label = readLabel(options.label, 'create');
    const detail = readDetail(options.detail, 'create');

    const icon = options.icon;
    if (typeof icon !== 'string' || !hasOwn(ICONS, icon)) {
      fail('create: icon must be one of ' + Object.keys(ICONS).join(', '));
    }

    const onActivate = isAbsent(options.onActivate) ? null : options.onActivate;
    if (onActivate !== null && typeof onActivate !== 'function') {
      fail('create: onActivate must be a function when provided');
    }

    const href = options.href;
    if (kind === 'button') {
      if (!isAbsent(href)) {
        fail('create: href is only allowed when kind is "link"');
      }
    } else if (typeof href !== 'string' || !(isSamePageHash(href) || isHttpsUrl(href))) {
      fail('create: link href must be a same-page "#fragment" or an absolute https:// URL');
    }

    return {
      kind: kind,
      label: label,
      detail: detail,
      icon: icon,
      href: kind === 'link' ? href : null,
      onActivate: onActivate
    };
  }

  function setAttributes(node, attributes) {
    Object.keys(attributes).forEach(function (name) {
      node.setAttribute(name, attributes[name]);
    });
  }

  function createSpan(className) {
    const span = document.createElement('span');
    span.className = className;
    return span;
  }

  function createIcon(shapes) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    setAttributes(svg, {
      'class': 'wa-symbol',
      'width': '20',
      'height': '20',
      'viewBox': '0 0 20 20',
      'fill': 'none',
      // currentColor matches the text color, including in Windows forced-colors mode.
      'stroke': 'currentColor',
      'stroke-width': '1.5',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
      // Decorative, so hidden from assistive tech. focusable="false" keeps
      // legacy engines from putting the SVG in the Tab order.
      'aria-hidden': 'true',
      'focusable': 'false'
    });
    shapes.forEach(function (shape) {
      const part = document.createElementNS(SVG_NS, shape[0]);
      setAttributes(part, shape[1]);
      svg.appendChild(part);
    });
    return svg;
  }

  function create(options) {
    const config = readOptions(options);
    const kind = config.kind;
    const isExternal = config.icon === 'external' ||
      (kind === 'link' && isHttpsUrl(config.href));
    idCounter += 1;
    const baseId = 'wa-row-' + idCounter;
    const listeners = [];
    let onActivate = config.onActivate;
    let disabled = false;
    let destroyed = false;
    let savedTabIndex = null;

    // Native elements provide the role, focusability and keyboard activation.
    // <button type="button"> activates on Enter and Space; <a href> activates on Enter.
    const element = document.createElement(kind === 'link' ? 'a' : 'button');
    if (kind === 'link') {
      element.setAttribute('href', config.href);
    } else {
      element.setAttribute('type', 'button');
    }
    element.className = 'wa-row wa-row--' + kind + (isExternal ? ' wa-row--external' : '');

    // The leading icon and trailing indicator are decorative (aria-hidden).
    // The row is always named by its visible label.
    const iconSlot = createSpan('wa-icon wa-row__icon--' + config.icon);
    iconSlot.setAttribute('aria-hidden', 'true');
    iconSlot.appendChild(createIcon(ICONS[config.icon]));

    const copy = createSpan('wa-copy');
    const labelNode = document.createElement('strong');
    labelNode.className = 'wa-row__label';
    labelNode.id = baseId + '-label';
    copy.appendChild(labelNode);

    // Created once and attached only while there is detail text, so node
    // identity stays the same across setLabels() calls.
    const detailGap = document.createTextNode(' ');
    const detailNode = document.createElement('small');
    detailNode.className = 'wa-row__detail';
    detailNode.id = baseId + '-detail';

    const trail = createSpan('wa-trailing wa-row__trail--' + (isExternal ? 'external' : 'chevron'));
    trail.setAttribute('aria-hidden', 'true');
    trail.appendChild(createIcon(isExternal ? TRAIL_ICONS.external : TRAIL_ICONS.chevron));

    element.appendChild(iconSlot);
    element.appendChild(copy);
    element.appendChild(trail);

    // The accessible name is the visible label only, so speech-input users
    // can say what they see. The detail is exposed as the accessible description.
    element.setAttribute('aria-labelledby', labelNode.id);

    function listen(type, handler) {
      element.addEventListener(type, handler);
      listeners.push([type, handler]);
    }

    function applyText(label, detail) {
      if (labelNode.textContent !== label) {
        labelNode.textContent = label;
      }
      if (detail) {
        if (detailNode.textContent !== detail) {
          detailNode.textContent = detail;
        }
        if (detailNode.parentNode !== copy) {
          copy.appendChild(detailGap);
          copy.appendChild(detailNode);
          element.setAttribute('aria-describedby', detailNode.id);
        }
      } else if (detailNode.parentNode === copy) {
        copy.removeChild(detailGap);
        copy.removeChild(detailNode);
        detailNode.textContent = '';
        element.removeAttribute('aria-describedby');
      }
    }

    function handleClick(event) {
      if (disabled) {
        // Disabled rows never activate. preventDefault blocks link navigation
        // and leaves the href in place. Stopping propagation matches a native
        // disabled button, which dispatches no click at all.
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      if (typeof onActivate !== 'function') {
        return; // No callback: native behavior applies (links follow their href).
      }
      if (kind === 'link') {
        // The caller handles navigation, so cancel the default hash change or
        // page load. Modifier keys are still readable on the event.
        event.preventDefault();
      }
      onActivate(event);
    }

    function handleLinkKeydown(event) {
      // Links activate on Enter only. A disabled link that already has focus
      // keeps it, because tabindex=-1 only removes it from the Tab order.
      // So Enter is cancelled here, and handleClick blocks any resulting click.
      if (disabled && event.key === 'Enter') {
        event.preventDefault();
      }
    }

    function handleLinkAuxclick(event) {
      // Middle-click would otherwise open the preserved href in a new window.
      if (disabled) {
        event.preventDefault();
      }
    }

    function setLabels(labels) {
      if (!isPlainObject(labels)) {
        fail('setLabels expects an object like { label, detail }');
      }
      assertKnownKeys(labels, LABEL_KEYS, 'setLabels');
      const label = readLabel(labels.label, 'setLabels');
      const detail = readDetail(labels.detail, 'setLabels');
      if (destroyed) {
        return;
      }
      // Text-only update. The element, listeners, callback and keyboard focus
      // are untouched, so switching language never moves focus.
      applyText(label, detail);
    }

    function setDisabled(value) {
      if (typeof value !== 'boolean') {
        fail('setDisabled expects a boolean');
      }
      if (destroyed || value === disabled) {
        return;
      }
      disabled = value;
      element.classList.toggle('wa-row--disabled', disabled);
      if (kind === 'button') {
        // Native disabled: exposed as unavailable, out of the Tab order, and
        // user clicks are suppressed. A focused button may lose focus, so
        // hosts should move focus if needed.
        element.disabled = disabled;
      } else if (disabled) {
        // Links have no native disabled state. aria-disabled announces it,
        // tabindex=-1 removes the link from the Tab order, and the handlers
        // above block click and Enter. The href is kept, so AT still sees a link.
        savedTabIndex = element.getAttribute('tabindex');
        element.setAttribute('aria-disabled', 'true');
        element.setAttribute('tabindex', '-1');
      } else {
        element.removeAttribute('aria-disabled');
        if (savedTabIndex === null) {
          element.removeAttribute('tabindex');
        } else {
          element.setAttribute('tabindex', savedTabIndex);
        }
        savedTabIndex = null;
      }
    }

    function destroy() {
      if (destroyed) {
        return;
      }
      destroyed = true;
      listeners.forEach(function (entry) {
        element.removeEventListener(entry[0], entry[1]);
      });
      listeners.length = 0;
      onActivate = null;
      if (element.parentNode) {
        element.parentNode.removeChild(element);
      }
    }

    applyText(config.label, config.detail);
    listen('click', handleClick);
    if (kind === 'link') {
      listen('keydown', handleLinkKeydown);
      listen('auxclick', handleLinkAuxclick);
    }

    return Object.freeze({
      element: element,
      setLabels: setLabels,
      setDisabled: setDisabled,
      destroy: destroy
    });
  }

  global.WorkbenchActionRow = Object.freeze({ create: create });
})(window);
