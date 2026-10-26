import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  CATALOGUES,
  LANG_NAMES,
  SUPPORTED,
  type Locale,
  type MessageKey,
} from "./catalogue";

export { LANG_NAMES, SUPPORTED };
export type { Locale, MessageKey };

type Vars = Record<string, string | number>;

interface I18nValue {
  locale: Locale;
  setLocale: (next: Locale) => void;
  t: (key: MessageKey, vars?: Vars) => string;
}

const I18nContext = createContext<I18nValue | null>(null);

function detectLocale(): Locale {
  const params = new URLSearchParams(window.location.search);
  const fromQuery = params.get("lang");
  if (fromQuery && (SUPPORTED as readonly string[]).includes(fromQuery)) {
    return fromQuery as Locale;
  }
  const cookie = document.cookie
    .split(";")
    .map((c) => c.trim().split("="))
    .find(([name]) => name === "lang")?.[1];
  if (cookie && (SUPPORTED as readonly string[]).includes(cookie)) {
    return cookie as Locale;
  }
  const nav = navigator.language.slice(0, 2);
  return (SUPPORTED as readonly string[]).includes(nav) ? (nav as Locale) : "en";
}

function interpolate(template: string, vars?: Vars): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in vars ? String(vars[key]) : match,
  );
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(detectLocale);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    // Mirror the choice into the cookie the server reads, so a full reload (or
    // a request that bypasses the SPA) keeps the same language.
    document.cookie = `lang=${next}; Max-Age=31536000; Path=/; SameSite=Lax`;
  }, []);

  const value = useMemo<I18nValue>(() => {
    const catalogue = CATALOGUES[locale];
    return {
      locale,
      setLocale,
      t: (key, vars) =>
        interpolate(catalogue[key] ?? CATALOGUES.en[key] ?? key, vars),
    };
  }, [locale, setLocale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used inside <I18nProvider>");
  return ctx;
}
