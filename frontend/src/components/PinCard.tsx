import { motion } from "framer-motion";

import type { Pin } from "../api/types";

/**
 * One masonry tile. Videos autoplay muted (matching the previous UI); the
 * `key`-stable element means a poll that re-renders the grid does not restart
 * playback.
 */
export function PinCard({
  pin,
  selected,
  selectable,
  onOpen,
  onToggle,
}: {
  pin: Pin;
  selected: boolean;
  selectable: boolean;
  onOpen: (pin: Pin) => void;
  onToggle: (pin: Pin) => void;
}) {
  return (
    <motion.figure
      layout
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.2 }}
      className={[
        "bg-card rounded-panel group relative overflow-hidden",
        selected ? "ring-accent ring-2" : "",
      ].join(" ")}
    >
      <button
        type="button"
        className="block w-full cursor-zoom-in"
        onClick={() => onOpen(pin)}
        aria-label={pin.title ?? pin.filename}
      >
        {pin.media_type === "video" ? (
          <video
            src={pin.url}
            className="w-full"
            muted
            loop
            autoPlay
            playsInline
            preload="metadata"
          />
        ) : (
          <img src={pin.url} alt={pin.title ?? pin.filename} className="w-full" loading="lazy" />
        )}
      </button>

      {selectable ? (
        <label className="absolute top-2 left-2 flex cursor-pointer items-center rounded-md bg-black/60 p-1.5">
          <input
            type="checkbox"
            className="accent-accent size-4"
            checked={selected}
            onChange={() => onToggle(pin)}
            aria-label={pin.filename}
          />
        </label>
      ) : null}

      {pin.dup_group !== null ? (
        <span className="absolute top-2 right-2 rounded-md bg-black/60 px-1.5 py-0.5 font-mono text-[10px] text-amber-300">
          DUP
        </span>
      ) : null}
    </motion.figure>
  );
}
