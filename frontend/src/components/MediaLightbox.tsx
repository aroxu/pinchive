import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useRef, useState } from "react";

import type { Pin } from "../api/types";
import { useI18n } from "../i18n";

const MIN_SCALE = 1;
const MAX_SCALE = 8;
// A pointer that travelled further than this was a drag, not a click-to-close.
const CLICK_SLOP = 6;

interface Transform {
  scale: number;
  x: number;
  y: number;
}

const IDENTITY: Transform = { scale: 1, x: 0, y: 0 };

/**
 * Full-bleed media viewer. The media is never cropped (object-contain) and the
 * backdrop, the X button, and Escape all close it. Clicking a video toggles
 * playback instead of closing, so scrubbing a clip does not dismiss the view.
 *
 * Images additionally zoom with the wheel (anchored on the cursor) and pan by
 * dragging once zoomed. A drag never closes the viewer: only a pointer that
 * stayed put counts as a click.
 */
export function MediaLightbox({
  pin,
  onClose,
}: {
  pin: Pin | null;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const stageRef = useRef<HTMLDivElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const [transform, setTransform] = useState<Transform>(IDENTITY);
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{
    id: number;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
    moved: number;
  } | null>(null);

  // Every new pin opens at its natural fit.
  useEffect(() => {
    setTransform(IDENTITY);
    setDragging(false);
    drag.current = null;
  }, [pin?.id]);

  /** Keep the image from being dragged off-screen; snap back to fit at 1x. */
  const clamp = useCallback((next: Transform): Transform => {
    if (next.scale <= MIN_SCALE) return IDENTITY;
    const stage = stageRef.current;
    const image = imageRef.current;
    if (!stage || !image) return next;
    // offsetWidth/Height are the untransformed layout size, so the bounds do
    // not feed back into themselves as the scale changes.
    const maxX = Math.max(0, (image.offsetWidth * next.scale - stage.clientWidth) / 2);
    const maxY = Math.max(0, (image.offsetHeight * next.scale - stage.clientHeight) / 2);
    return {
      scale: next.scale,
      x: Math.min(maxX, Math.max(-maxX, next.x)),
      y: Math.min(maxY, Math.max(-maxY, next.y)),
    };
  }, []);

  /** Zoom by `factor` while keeping the point under the cursor stationary. */
  const zoomAt = useCallback(
    (factor: number, clientX: number, clientY: number) => {
      setTransform((prev) => {
        const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, prev.scale * factor));
        if (scale === prev.scale) return prev;
        const stage = stageRef.current;
        if (!stage) return clamp({ ...prev, scale });
        const rect = stage.getBoundingClientRect();
        const px = clientX - (rect.left + rect.width / 2);
        const py = clientY - (rect.top + rect.height / 2);
        const ratio = scale / prev.scale;
        return clamp({
          scale,
          x: px * (1 - ratio) + prev.x * ratio,
          y: py * (1 - ratio) + prev.y * ratio,
        });
      });
    },
    [clamp],
  );

  // React routes onWheel through a passive root listener, so preventDefault
  // there is ignored — bind the wheel natively instead.
  useEffect(() => {
    const stage = stageRef.current;
    if (!pin || pin.media_type === "video" || !stage) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      // Exponential so trackpad pixels and mouse notches both feel linear.
      // deltaY is clamped first: a single mouse notch can report hundreds of
      // pixels (and DOM_DELTA_LINE/PAGE report far larger units), which would
      // otherwise jump straight to the maximum zoom in one flick.
      const step = Math.max(-60, Math.min(60, e.deltaY));
      zoomAt(Math.exp(-step * 0.0035), e.clientX, e.clientY);
    };
    stage.addEventListener("wheel", onWheel, { passive: false });
    return () => stage.removeEventListener("wheel", onWheel);
  }, [pin, zoomAt]);

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

  const zoomed = transform.scale > MIN_SCALE;

  const onPointerDown = (e: React.PointerEvent<HTMLImageElement>) => {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = {
      id: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      originX: transform.x,
      originY: transform.y,
      moved: 0,
    };
  };

  const onPointerMove = (e: React.PointerEvent<HTMLImageElement>) => {
    const active = drag.current;
    if (!active || active.id !== e.pointerId) return;
    const dx = e.clientX - active.startX;
    const dy = e.clientY - active.startY;
    active.moved = Math.max(active.moved, Math.hypot(dx, dy));
    if (!zoomed) return; // nothing to pan while the image already fits
    if (!dragging && active.moved > CLICK_SLOP) setDragging(true);
    setTransform((prev) =>
      clamp({ ...prev, x: active.originX + dx, y: active.originY + dy }),
    );
  };

  const onPointerUp = (e: React.PointerEvent<HTMLImageElement>) => {
    const active = drag.current;
    drag.current = null;
    setDragging(false);
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    // Only a pointer that stayed put is a click, so panning never dismisses.
    if (active && active.moved <= CLICK_SLOP) onClose();
  };

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
            ref={stageRef}
            className="relative flex h-full w-full items-center justify-center overflow-hidden"
            initial={{ scale: 0.94, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.96, opacity: 0 }}
            transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
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
                  // Must not bubble to the closing backdrop.
                  e.stopPropagation();
                  const el = e.currentTarget;
                  if (el.paused) void el.play();
                  else el.pause();
                }}
              />
            ) : (
              <img
                ref={imageRef}
                src={pin.url}
                alt={pin.title ?? pin.filename}
                draggable={false}
                className="max-h-full max-w-full object-contain select-none"
                style={{
                  transform: `translate3d(${transform.x}px, ${transform.y}px, 0) scale(${transform.scale})`,
                  // Animating while panning would lag the image behind the cursor.
                  transition: dragging
                    ? "none"
                    : "transform 0.12s cubic-bezier(0.22, 1, 0.36, 1)",
                  cursor: zoomed ? (dragging ? "grabbing" : "grab") : "zoom-in",
                  touchAction: "none",
                }}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerCancel={onPointerUp}
                // Closing is decided in onPointerUp; swallow the click so a
                // drag that ends on the image cannot reach the backdrop.
                onClick={(e) => e.stopPropagation()}
              />
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
