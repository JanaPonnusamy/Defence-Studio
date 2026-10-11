// Reusable inline-SVG icon primitive (registry UI-06). One canonical copy of
// the interface iconography so views render consistent 24x24 line icons that
// inherit `currentColor` -- never an emoji used as an interface control.
//
// All paths are stroke-based (fill: none) on a 0 0 24 24 viewBox so they align
// with text and scale cleanly. Keep this set small and semantic; add a new
// named icon here rather than inlining one-off SVG in a view.

const PATHS = {
  // Discovery: radar sweep
  radar: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/><path d="M12 12 19 6"/><path d="M12 3v2.5"/>',
  // Sources: stacked layers
  layers: '<path d="M12 3 21 8l-9 5-9-5 9-5Z"/><path d="M3 13l9 5 9-5"/>',
  // Claims: document with a scan line (verbatim spans)
  claims: '<path d="M6 3h9l4 4v14H6Z"/><path d="M15 3v4h4"/><path d="M9 12h6"/><path d="M9 16h4"/>',
  // Evidence: shield with check (approval/package)
  shield: '<path d="M12 3 20 6v6c0 5-3.5 7.5-8 9-4.5-1.5-8-4-8-9V6Z"/><path d="M9 12l2 2 4-4"/>',
  // Script: narration lines
  script: '<path d="M5 4h11l3 3v13H5Z"/><path d="M8 9h8"/><path d="M8 13h8"/><path d="M8 17h5"/>',
  // Theme toggles
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z"/>',
};

/**
 * Returns an inline SVG string for a named icon. Unknown names return "" so a
 * missing icon degrades to text rather than throwing.
 */
export function icon(name, { size = 18, cls = "" } = {}) {
  const d = PATHS[name];
  if (!d) return "";
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none"
    stroke="currentColor" stroke-width="1.6" stroke-linecap="round"
    stroke-linejoin="round" aria-hidden="true" focusable="false"${cls ? ` class="${cls}"` : ""}>${d}</svg>`;
}

export const ICON_NAMES = Object.keys(PATHS);
