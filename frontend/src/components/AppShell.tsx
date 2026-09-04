import { Button } from "@heroui/react";
import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { api } from "../api/client";
import { useQuery } from "../hooks/useQuery";
import { useI18n, type MessageKey } from "../i18n";

const NAV: { to: string; key: MessageKey; badge?: "syncing" | "removable" }[] = [
  { to: "/", key: "nav.boards", badge: "syncing" },
  { to: "/duplicates", key: "nav.duplicates", badge: "removable" },
  { to: "/settings", key: "nav.settings" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const location = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Counters drive the nav badges, so they refresh while a sync runs.
  const { data: counts } = useQuery(() => api.sidebar(), [], { pollMs: 5000 });

  // A navigation should always close the mobile drawer.
  useEffect(() => setDrawerOpen(false), [location.pathname]);

  const nav = (
    <nav className="flex flex-col gap-1">
      {NAV.map((item) => {
        const badge =
          item.badge === "syncing"
            ? counts?.syncing
            : item.badge === "removable"
              ? counts?.removable
              : undefined;
        return (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) =>
              [
                "relative flex items-center justify-between rounded-[10px] px-3 py-2 text-sm transition-colors",
                isActive
                  ? "bg-accent-soft text-ink font-semibold"
                  : "text-ink-dim hover:bg-card-raised hover:text-ink",
              ].join(" ")
            }
          >
            {({ isActive }) => (
              <>
                {isActive && (
                  // layoutId animates the accent rail between nav items.
                  <motion.span
                    layoutId="nav-rail"
                    className="absolute top-1 bottom-1 left-0 w-[3px] rounded-full bg-accent"
                    transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
                  />
                )}
                <span>{t(item.key)}</span>
                {badge ? (
                  <span className="ml-2 rounded-full bg-card-raised px-2 py-0.5 font-mono text-xs text-accent">
                    {badge}
                  </span>
                ) : null}
              </>
            )}
          </NavLink>
        );
      })}
    </nav>
  );

  const footer = (
    <div className="border-line border-t pt-4 text-xs">
      <div className="text-ink">{t("sidebar.sessions", { n: counts?.creds ?? 0 })}</div>
      <div className="text-ink-dim">
        {t("sidebar.active", { n: counts?.creds_active ?? 0 })}
      </div>
    </div>
  );

  return (
    // The shell is pinned to the viewport so the sidebar and the mobile bar
    // stay put; only the content column scrolls. Height is dvh (with a vh
    // fallback): on phones 100vh counts the retracted browser chrome, which
    // pushed the bottom of the app under the address bar.
    <div className="app-shell flex overflow-hidden">
      {/* Desktop sidebar */}
      <aside className="bg-panel border-line hidden h-full w-60 shrink-0 flex-col justify-between overflow-y-auto border-r p-5 lg:flex">
        <div className="flex flex-col gap-6">
          <NavLink to="/" className="flex flex-col">
            <span className="text-lg font-bold tracking-tight">{t("app.name")}</span>
            <span className="text-ink-dim text-xs">{t("app.tagline")}</span>
          </NavLink>
          {nav}
        </div>
        {footer}
      </aside>

      <div className="flex min-w-0 min-h-0 flex-1 flex-col">
        {/* Mobile top bar */}
        <header className="bg-panel border-line flex shrink-0 items-center gap-2 border-b px-3 py-3 lg:hidden">
          <Button
            size="sm"
            variant="ghost"
            className="-ml-1"
            aria-label={t("nav.boards")}
            aria-expanded={drawerOpen}
            onPress={() => setDrawerOpen((open) => !open)}
          >
            <motion.svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              aria-hidden="true"
              animate={{ rotate: drawerOpen ? 90 : 0 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
            >
              <path
                d={drawerOpen ? "M6 6l12 12M18 6L6 18" : "M4 7h16M4 12h16M4 17h16"}
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                fill="none"
              />
            </motion.svg>
          </Button>
          <NavLink to="/" className="font-bold">
            {t("app.name")}
          </NavLink>
        </header>

        <AnimatePresence>
          {drawerOpen && (
            <motion.div
              initial={{ height: 0, opacity: 0, y: -8 }}
              animate={{ height: "auto", opacity: 1, y: 0 }}
              exit={{ height: 0, opacity: 0, y: -8 }}
              transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
              className="bg-panel border-line shrink-0 overflow-hidden border-b px-4 lg:hidden"
            >
              <div className="py-3">{nav}</div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* The one scroll container in the app; the lightbox locks it by this
            attribute while it is open. */}
        <div
          data-app-scroll
          className="min-w-0 flex-1 overflow-y-auto px-4 pt-6 pb-[calc(1.5rem+env(safe-area-inset-bottom))] sm:px-6 lg:px-8"
        >
          {children}
        </div>
      </div>
    </div>
  );
}
