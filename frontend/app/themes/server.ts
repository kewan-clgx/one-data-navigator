import "server-only";

import { resolveTheme, type Theme } from ".";

/** The deployment's theme, chosen with ODN_THEME (unknown or unset values fall back to the default). */
export function currentTheme(): Theme {
  return resolveTheme(process.env.ODN_THEME);
}
