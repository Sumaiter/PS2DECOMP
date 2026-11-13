'use strict';
const catalog = require('./media/localization');
let language = 'en';
function translate(value) { return catalog.translate(value, language); }
function presentation(value) {
  if (typeof value === 'string') return translate(value);
  if (Array.isArray(value)) {value.forEach((item, index) => {value[index] = presentation(item);}); return value;}
  if (!value || Object.getPrototypeOf(value) !== Object.prototype) return value;
  for (const key of ['label', 'title', 'description', 'detail', 'tooltip', 'placeholder', 'prompt', 'message', 'text']) {
    if (typeof value[key] === 'string') value[key] = translate(value[key]);
  }
  if (value.buttons) presentation(value.buttons);
  return value;
}
function html(value) {
  value = value.replace(/<html lang="[^"]*"/, '<html lang="' + language + '"');
  // Text is translated in the DOM; code, attributes and message identifiers are
  // never rewritten by string substitution in an HTML source document.
  return value.replace(/(<script nonce="([^"]+)" src="([^"]+\/)(?:panel|automatic)\.js"[^>]*>)/,
    '<script nonce="$2" src="$3localization.js"></script>$1');
}
function localizedVscode(api) {
  language = /^de(?:-|$)/i.test(api.env.language) ? 'de' : 'en';
  const cache = new WeakMap();
  const wrap = object => {
    if (cache.has(object)) return cache.get(object);
    const proxy = new Proxy(object, {
    set(target, key, value) {
      target[key] = key === 'html' ? html(value) : ['title', 'placeholder', 'prompt', 'items', 'buttons'].includes(key) ? presentation(value) : value;
      return true;
    },
    get(target, key) {
      const value = target[key];
      if (key === 'webview') return wrap(value);
      if (typeof value !== 'function') return value;
      return (...args) => {
        if (['showErrorMessage', 'showWarningMessage', 'showInformationMessage', 'showQuickPick', 'showInputBox', 'withProgress', 'createWebviewPanel', 'createOutputChannel', 'appendText', 'report'].includes(key)) args = args.map(presentation);
        if (key === 'withProgress') {
          const callback = args[1];
          args[1] = (progress, token) => callback(wrap(progress), token);
        }
        const result = value.apply(target, args);
        if (result === target) return proxy;
        return ['createQuickPick', 'createWebviewPanel'].includes(key) ? wrap(result) : result;
      };
    }
    });
    cache.set(object, proxy);
    return proxy;
  };
  return new Proxy(api, {get(target, key) {
    if (key === 'window') return wrap(target.window);
    if (key === 'MarkdownString' || key === 'Diagnostic') return new Proxy(target[key], {
      construct(ctor, args) {return wrap(Reflect.construct(ctor, args.map(presentation)));}
    });
    return target[key];
  }});
}
module.exports = {translate, localizedVscode, presentation, html};
