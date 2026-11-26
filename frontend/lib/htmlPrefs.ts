/**
 * Look preferences stored per browser and applied as `data-*` attributes on <html> (read by app/themes/*.css).
 * Shared by the client store (displayPrefs.ts) and the pre-paint script in the root layout.
 */
export const APPEARANCE_KEY = "odn.appearance";
export const APPEARANCE_IDS = ["elegant", "code-dark", "code-light"] as const;
export const CODE_THEME_KEY = "odn.sql.theme";
export const CODE_THEME_IDS = ["auto", "elegant", "vscode-dark", "vscode-light"] as const;

/** Inline <head> script: applies the saved choices before first paint, so a dark appearance never flashes light. */
export const HTML_PREFS_SCRIPT = `(function(){try{var d=document.documentElement,s=localStorage;var a=s.getItem(${JSON.stringify(
  APPEARANCE_KEY,
)});if(${JSON.stringify(APPEARANCE_IDS.slice(1))}.indexOf(a)>=0)d.dataset.appearance=a;var c=s.getItem(${JSON.stringify(
  CODE_THEME_KEY,
)});if(${JSON.stringify(CODE_THEME_IDS.slice(1))}.indexOf(c)>=0)d.dataset.codeTheme=c;}catch(e){}})();`;
