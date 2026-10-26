import type { ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { I18nProvider, useI18n, type Locale } from "./index";

const wrapper = ({ children }: { children: ReactNode }) => (
  <I18nProvider>{children}</I18nProvider>
);

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  document.cookie = "lang=; Max-Age=0; Path=/";
  document.documentElement.lang = "en";
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("I18nProvider document language", () => {
  it.each<{
    source: string;
    query?: string;
    cookie?: string;
    browser: string;
    locale: Locale;
  }>([
    { source: "query before cookie and browser", query: "ko", cookie: "en", browser: "en-US", locale: "ko" },
    { source: "cookie before browser", cookie: "ko", browser: "en-US", locale: "ko" },
    { source: "browser", browser: "ko-KR", locale: "ko" },
    { source: "cookie after unsupported query", query: "fr", cookie: "ko", browser: "en-US", locale: "ko" },
    { source: "browser after unsupported cookie", cookie: "fr", browser: "ko-KR", locale: "ko" },
    { source: "explicit English before Korean cookie", query: "en", cookie: "ko", browser: "ko-KR", locale: "en" },
    { source: "English fallback", browser: "fr-FR", locale: "en" },
  ])("sets the initial document language from $source", ({ query, cookie, browser, locale }) => {
    if (query) window.history.replaceState(null, "", `/?lang=${query}`);
    if (cookie) document.cookie = `lang=${cookie}; Path=/`;
    vi.spyOn(window.navigator, "language", "get").mockReturnValue(browser);
    // Start from the opposite language, also verifying an English detection
    // corrects an existing Korean document rather than relying on index.html.
    document.documentElement.lang = locale === "ko" ? "en" : "ko";

    const { result } = renderHook(useI18n, { wrapper, reactStrictMode: true });
    expect(result.current.locale).toBe(locale);
    expect(document.documentElement.lang).toBe(locale);
    expect(result.current.t("nav.boards")).toBe(locale === "ko" ? "보드" : "Boards");
  });

  it("keeps document language, translations, and the saved cookie in sync on switches", () => {
    vi.spyOn(window.navigator, "language", "get").mockReturnValue("ko-KR");
    const { result, rerender, unmount } = renderHook(useI18n, { wrapper, reactStrictMode: true });
    expect(document.documentElement.lang).toBe("ko");
    const setLocale = result.current.setLocale;

    act(() => { setLocale("en"); });
    expect(result.current.locale).toBe("en");
    expect(result.current.t("nav.boards")).toBe("Boards");
    expect(document.documentElement.lang).toBe("en");
    expect(document.cookie).toContain("lang=en");

    rerender();
    expect(result.current.setLocale).toBe(setLocale);
    act(() => { setLocale("ko"); });
    expect(result.current.locale).toBe("ko");
    expect(result.current.t("nav.boards")).toBe("보드");
    expect(document.documentElement.lang).toBe("ko");
    expect(document.cookie).toContain("lang=ko");

    unmount();
    vi.spyOn(window.navigator, "language", "get").mockReturnValue("en-US");
    const remounted = renderHook(useI18n, { wrapper, reactStrictMode: true });
    expect(remounted.result.current.locale).toBe("ko");
    expect(document.documentElement.lang).toBe("ko");
  });
});
