import { AnimatePresence, motion } from "framer-motion";
import { Route, Routes, useLocation } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { useI18n } from "./i18n";
import { BoardDetailPage } from "./pages/BoardDetailPage";
import { BoardsPage } from "./pages/BoardsPage";
import { DuplicatesPage } from "./pages/DuplicatesPage";
import { SettingsPage } from "./pages/SettingsPage";

/** Screen transition: a short cross-fade with a small vertical offset. Framer
 *  Motion honours `prefers-reduced-motion` when the flag is set on the tree, so
 *  the shift is skipped for users who ask for less motion. */
const pageMotion = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -8 },
  transition: { duration: 0.22, ease: [0.22, 1, 0.36, 1] as const },
};

export function App() {
  const location = useLocation();
  const { locale } = useI18n();

  return (
    <AppShell>
      {/* mode="wait" lets the outgoing screen finish before the next one enters,
          so the two never overlap and shift the scroll position. */}
      <AnimatePresence mode="wait" initial={false}>
        {/* The locale is part of the key: switching language must re-mount the
            screen, otherwise `mode="wait"` keeps presenting the already-exited
            copy and the new strings never appear. */}
        <motion.main
          key={`${locale}:${location.pathname}`}
          {...pageMotion}
          className="min-h-full"
        >
          <Routes location={location}>
            <Route path="/" element={<BoardsPage />} />
            <Route path="/boards/:boardId" element={<BoardDetailPage />} />
            <Route path="/duplicates" element={<DuplicatesPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<BoardsPage />} />
          </Routes>
        </motion.main>
      </AnimatePresence>
    </AppShell>
  );
}
