(function (scope) {
  'use strict';
  const defaults = Object.freeze({minBytes: 8, maxBytes: 640});
  const maxBytes = 32768; // Same supported preflight window as function_size.MAX_BYTES.
  // Selection suggestions, not claims about complexity or decompilation success.
  const presets = Object.freeze([
    {id: 'tiny', label: 'Tiny', minBytes: 8, maxBytes: 64},
    {id: 'small', label: 'Small', minBytes: 65, maxBytes: 256},
    {id: 'medium', label: 'Medium', minBytes: 257, maxBytes: 640},
    {id: 'big', label: 'Big', minBytes: 641, maxBytes: 2048},
    {id: 'giant', label: 'Giant', minBytes: 2049, maxBytes}
  ].map(Object.freeze));
  function parse(value = {}) {
    const validObject = value !== null && typeof value === 'object' && !Array.isArray(value);
    const range = validObject ? {minBytes: value.minBytes === undefined ? defaults.minBytes : value.minBytes,
      maxBytes: value.maxBytes === undefined ? defaults.maxBytes : value.maxBytes} : {};
    if (!Number.isSafeInteger(range.minBytes) || !Number.isSafeInteger(range.maxBytes) ||
        range.minBytes < 0 || range.maxBytes < 8 || range.minBytes > range.maxBytes || range.maxBytes > maxBytes) {
      throw new Error('Ungültiger Byte-Bereich: ganze Zahlen von 0 bis 32768, bis mindestens 8 und von nicht größer als bis.');
    }
    return range;
  }
  const api = {defaults, maxBytes, presets, parse};
  if (typeof module !== 'undefined') module.exports = api;
  else scope.PS2DECOMP_SIZE_RANGE = api;
})(typeof window === 'undefined' ? globalThis : window);
