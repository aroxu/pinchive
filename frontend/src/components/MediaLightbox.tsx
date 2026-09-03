import { AnimatePresence, motion } from "framer-motion";
import { useEffect } from "react";

import type { Pin } from "../api/types";
import { useI18n } from "../i18n";

/**
 * Full-bleed media viewer. The media is never cropped (object-contain) and the
 * backdrop, the X button, and Escape all close it. Clicking a video toggles
 * playback instead of closing, so scrubbing a clip does not dismiss the view.
 */
export function MediaLightbox({
  pin,
  onClose,
}: {
  pin: Pin | null;
  onClose: () => void;
}) {
  const { t } = useI18n();

  useEffect(() => {
    if (!pin) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    // Freeze the page behind the overlay. The app shell scrolls its content
    // column, not <body>, so locking body overflow here would do nothing —
    // target the actual scroll container.
    const scroller = document.querySelector<HTMLElement>("[data-app-scroll]");
    const previous = scroller?.style.overflowY ?? "";
    if (scroller) scroller.style.overflowY = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      if (scroller) scroller.style.overflowY = previous;
    };
  }, [pin, onClose]);

  return (
    <AnimatePresence>
      {pin && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
          onClick={onClose}
          role="dialog"
          aria-modal="true"
        >
          <button
            type="button"
            aria-label={t("common.close")}
            className="absolute top-4 right-4 z-10 rounded-full bg-white/10 px-3 py-1.5 text-lg text-white hover:bg-white/20"
            onClick={onClose}
          >
            ✕
          </button>
          <motion.div
            className="flex max-h-full max-w-full items-center justify-center"
            initial={{ scale: 0.94, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.96, opacity: 0 }}
            transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
            // Clicks inside the media must not bubble to the closing backdrop.
            onClick={(e) => e.stopPropagation()}
          >
            {pin.media_type === "video" ? (
              <video
                src={pin.url}
                className="max-h-[88vh] max-w-full object-contain"
                controls
                autoPlay
                loop
                playsInline
                onClick={(e) => {
                  const el = e.currentTarget;
                  if (el.paused) void el.play();
                  else el.pause();
                }}
              />
            ) : (
              <img
                src={pin.url}
                alt={pin.title ?? pin.filename}
                className="max-h-[88vh] max-w-full object-contain"
                onClick={onClose}
              />
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
