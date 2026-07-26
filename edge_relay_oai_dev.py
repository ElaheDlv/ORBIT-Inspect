import csv
import json
import os
import socket
import time
import threading
from pathlib import Path
from typing import Dict, Any, Optional

import cv2
import numpy as np
import torch
from ultralytics import YOLO


LISTEN_IP = os.getenv("EDGE_LISTEN_IP", "0.0.0.0")
LISTEN_PORT = int(os.getenv("EDGE_LISTEN_PORT", "9100"))
SOCKET_TIMEOUT_SEC = float(os.getenv("EDGE_SOCKET_TIMEOUT_SEC", "30.0"))

# ------------------------------------------------
# Concurrency limits
# ------------------------------------------------
MAX_EDGE_CONNECTION_THREADS = 2
MAX_EDGE_YOLO_WORKERS = 2

_inference_sem = threading.Semaphore(MAX_EDGE_YOLO_WORKERS)
_model_lock = threading.Lock()


# -----------------------------
# Model config
# -----------------------------
# Change this to your trained checkpoint
MODEL_PATH = os.getenv("MODEL_PATH", "best_n.pt")
RUN_SPEED = float(os.getenv("RUN_SPEED", "1.0"))
EDGE_PRELOAD_MODEL = os.getenv("EDGE_PRELOAD_MODEL", "1").lower() not in {"0", "false", "no"}
EDGE_WARMUP_INFERENCE = os.getenv("EDGE_WARMUP_INFERENCE", "1").lower() not in {"0", "false", "no"}
EDGE_WARMUP_IMAGE_WIDTH = int(os.getenv("EDGE_WARMUP_IMAGE_WIDTH", "640"))
EDGE_WARMUP_IMAGE_HEIGHT = int(os.getenv("EDGE_WARMUP_IMAGE_HEIGHT", "640"))

# ------------------------------------------------
# Stage 1 edge-resource metadata / CPU-thread control
# ------------------------------------------------
EDGE_LABEL = os.getenv("EDGE_LABEL", "")
EDGE_CPUS = os.getenv("EDGE_CPUS", "")
EDGE_COMPUTE_CAPACITY = os.getenv("EDGE_COMPUTE_CAPACITY", "")
EDGE_RESOURCE_CONTROL = os.getenv("EDGE_RESOURCE_CONTROL", "")
EDGE_TORCH_THREADS = int(os.getenv("EDGE_TORCH_THREADS", "4"))

if EDGE_TORCH_THREADS > 0:
    torch.set_num_threads(EDGE_TORCH_THREADS)
    torch.set_num_interop_threads(max(1, min(EDGE_TORCH_THREADS, 4)))

CONF_THRESHOLD = 0.25
IOU_THRESHOLD = 0.45

# Optional ROI if you want to crop before inference
USE_ROI = False
ROI = (517, 220, 795, 497)

# ------------------------------------------------
# Optional ROI rotation before YOLO inference
# ------------------------------------------------
# This is applied AFTER edge-side ROI extraction and BEFORE model.predict().
# PIL rotate(-90, expand=True) == OpenCV ROTATE_90_CLOCKWISE.
ROTATE_ROI_BEFORE_INFERENCE = True

# Options:
#   "cw90"  = clockwise 90 degrees, same as PIL rotate(-90, expand=True)
#   "ccw90" = counter-clockwise 90 degrees, same as PIL rotate(90, expand=True)
#   "180"   = rotate 180 degrees
#   "none"  = no rotation, equivalent to ROTATE_ROI_BEFORE_INFERENCE = False
ROI_ROTATION_MODE = "cw90"

SAVE_DEBUG_IMAGES = False
# DEBUG_DIR = Path("./neu_yolo_debug_oai").expanduser()

WRITE_EDGE_CSV = True
# EDGE_CSV_PATH = Path("./edge_results_multi_neu.csv").expanduser()

model_name = Path(MODEL_PATH).stem   # "last_n"

DEBUG_DIR = Path(os.getenv("EDGE_DEBUG_DIR", f"./neu_yolo_debug_oai_dev_{model_name}_{RUN_SPEED}")).expanduser()
EDGE_CSV_PATH = Path(os.getenv("EDGE_CSV_PATH", f"./edge_results_oai_dev_multi_neu_{model_name}_{RUN_SPEED}.csv")).expanduser()


# Edge-side ROI via blue-conveyor color segmentation
# ------------------------------------------------
# The factory now sends the full frame.
# The edge crops the part ROI before YOLO inference.
USE_BACKGROUND_ROI = os.getenv("EDGE_USE_BACKGROUND_ROI", "1").lower() not in {"0", "false", "no"}
BACKGROUND_DIR = Path("./backgrounds")  # kept only for backward compatibility; not used by blue ROI

# Blue conveyor HSV range. Tune only if your belt color changes.
# OpenCV HSV: H in [0,179], S/V in [0,255]
BELT_BLUE_LOWER_HSV = np.array([90, 50, 30], dtype=np.uint8)
BELT_BLUE_UPPER_HSV = np.array([145, 255, 255], dtype=np.uint8)

# Part ROI constraints
ROI_MIN_AREA = 1500
ROI_MAX_AREA_FRAC = 0.75       # reject huge contours such as rails/floor
ROI_MIN_CONTOUR_AREA = 80
ROI_MORPH_OPEN_KSIZE = 3
ROI_MORPH_CLOSE_KSIZE = 7
ROI_PADDING = 0                # IMPORTANT: no padding, so no blue belt is included
ROI_EDGE_SHRINK_PX = 5         # trims inside bbox to remove any remaining blue border
ROI_FALLBACK_TO_FULL_FRAME = False

# If strict centered/rectangular ROI fails, use the best available tight non-blue crop,
# but log it as a fallback/low-quality ROI.
ROI_USE_FALLBACK_BEST_AVAILABLE = True
ROI_FALLBACK_MIN_AREA = 250

# Shape/position validation for the new rectangular part.
# aspect = bbox_width / bbox_height.
# For a 2048x1000 rectangular texture/part, expected aspect is about 2.05.
# If your camera sees the part rotated 90 degrees, enable ROI_ALLOW_ROTATED_RECTANGLE.
ROI_REQUIRE_RECTANGULAR_LIKE = True
ROI_EXPECTED_ASPECT = 2048.0 / 1000.0
ROI_ASPECT_TOL = 0.35          # 35% tolerance around expected aspect
ROI_ALLOW_ROTATED_RECTANGLE = True
ROI_REJECT_BORDER_TOUCH = True
ROI_BORDER_MARGIN_PX = 2
ROI_REQUIRE_CENTERED = True
ROI_CENTER_TOL_FRAC = 0.30     # allowed distance from image center as fraction of min(image W,H)

# Do NOT resize here; keep payload tied to actual cropped ROI size for your communication story.
ROI_RESIZE_TO_FIXED = False

SAVE_ROI_DEBUG = False
ROI_DEBUG_DIR = Path(f"./factory_roi_debug_{model_name}_{RUN_SPEED}")



# Edge ROI debug directory. This is separate from YOLO debug images.
EDGE_ROI_DEBUG_DIR = Path(os.getenv("EDGE_ROI_DEBUG_DIR", f"./edge_roi_debug_oai_dev_{model_name}_{RUN_SPEED}")).expanduser()

class BackgroundROIDetector:
    """
    Blue-conveyor ROI detector.

    This class intentionally keeps the old class name and constructor arguments
    so the rest of your original factory relay code does not need to change.

    Pipeline:
      1) Detect pixels that are NOT conveyor-blue in HSV.
      2) Remove noise with morphology.
      3) Select the best rectangular, non-border contour.
      4) Crop tightly to the part only.
      5) Trim any remaining blue border and shrink inward a few pixels.

    No resizing is done here, so communication payload size remains meaningful.
    """
    def __init__(
        self,
        background_dir: Optional[Path] = None,   # unused; kept for compatibility
        diff_threshold: int = 12,                # unused; kept for compatibility
        min_area: int = 1500,
        min_contour_area: int = 80,
        blur_ksize: int = 5,                    # unused; kept for compatibility
        morph_open_ksize: int = 3,
        morph_close_ksize: int = 7,
        padding: int = 0,
        debug_dir: Optional[Path] = None,
    ):
        self.min_area = int(min_area)
        self.min_contour_area = int(min_contour_area)
        self.morph_open_ksize = int(morph_open_ksize)
        self.morph_close_ksize = int(morph_close_ksize)
        self.padding = int(padding)
        self.debug_dir = Path(debug_dir) if debug_dir is not None else None

        if self.debug_dir is not None:
            self.debug_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _ensure_odd(k: int) -> int:
        return k if k % 2 == 1 else k + 1

    @staticmethod
    def _clip_bbox(x1, y1, x2, y2, w, h):
        x1 = max(0, min(int(x1), w - 1))
        y1 = max(0, min(int(y1), h - 1))
        x2 = max(x1 + 1, min(int(x2), w))
        y2 = max(y1 + 1, min(int(y2), h))
        return x1, y1, x2, y2

    def _make_non_blue_mask(self, frame_bgr):
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)

        blue_mask = cv2.inRange(hsv, BELT_BLUE_LOWER_HSV, BELT_BLUE_UPPER_HSV)
        non_blue_mask = cv2.bitwise_not(blue_mask)

        open_k = self._ensure_odd(self.morph_open_ksize)
        close_k = self._ensure_odd(self.morph_close_ksize)

        if open_k > 1:
            kernel_open = cv2.getStructuringElement(cv2.MORPH_RECT, (open_k, open_k))
            non_blue_mask = cv2.morphologyEx(non_blue_mask, cv2.MORPH_OPEN, kernel_open)

        if close_k > 1:
            kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (close_k, close_k))
            non_blue_mask = cv2.morphologyEx(non_blue_mask, cv2.MORPH_CLOSE, kernel_close)

        return non_blue_mask, blue_mask

    def _is_border_touching(self, x, y, w_box, h_box, frame_w, frame_h):
        m = int(ROI_BORDER_MARGIN_PX)
        return (
            x <= m or y <= m or
            (x + w_box) >= (frame_w - m) or
            (y + h_box) >= (frame_h - m)
        )

    def _aspect_allowed(self, aspect: float) -> bool:
        """Check whether bbox aspect matches the expected rectangular part."""
        if not ROI_REQUIRE_RECTANGULAR_LIKE:
            return True

        aspect = float(max(aspect, 1e-6))
        targets = [float(ROI_EXPECTED_ASPECT)]
        if ROI_ALLOW_ROTATED_RECTANGLE:
            targets.append(1.0 / float(ROI_EXPECTED_ASPECT))

        for target in targets:
            lo = target * (1.0 - float(ROI_ASPECT_TOL))
            hi = target * (1.0 + float(ROI_ASPECT_TOL))
            if lo <= aspect <= hi:
                return True
        return False

    def _aspect_score(self, aspect: float) -> float:
        """Score aspect ratio using closeness to expected rectangle, including optional 90-degree rotation."""
        aspect = float(max(aspect, 1e-6))
        targets = [float(ROI_EXPECTED_ASPECT)]
        if ROI_ALLOW_ROTATED_RECTANGLE:
            targets.append(1.0 / float(ROI_EXPECTED_ASPECT))

        # Log-ratio error treats 2.0 vs 1.0 and 0.5 vs 1.0 symmetrically.
        best_err = min(abs(np.log(aspect / max(t, 1e-6))) for t in targets)
        tol_err = abs(np.log(1.0 + float(ROI_ASPECT_TOL)))
        return float(max(0.0, 1.0 - best_err / max(tol_err, 1e-6)))

    def _score_contour(self, c, frame_w, frame_h):
        area = float(cv2.contourArea(c))
        if area < self.min_contour_area or area < self.min_area:
            return None

        frame_area = float(frame_w * frame_h)
        if area > ROI_MAX_AREA_FRAC * frame_area:
            return None

        x, y, w_box, h_box = cv2.boundingRect(c)
        if w_box <= 1 or h_box <= 1:
            return None

        if ROI_REJECT_BORDER_TOUCH and self._is_border_touching(x, y, w_box, h_box, frame_w, frame_h):
            return None

        aspect = w_box / float(h_box)
        if not self._aspect_allowed(aspect):
            return None

        cx = x + w_box / 2.0
        cy = y + h_box / 2.0
        frame_cx = frame_w / 2.0
        frame_cy = frame_h / 2.0
        center_dist = ((cx - frame_cx) ** 2 + (cy - frame_cy) ** 2) ** 0.5
        center_tol = ROI_CENTER_TOL_FRAC * min(frame_w, frame_h)

        if ROI_REQUIRE_CENTERED and center_dist > center_tol:
            return None

        aspect_score = self._aspect_score(aspect)
        area_score = area / frame_area
        center_score = 1.0 - min(center_dist / max(center_tol, 1.0), 1.0)
        score = 3.0 * area_score + 1.5 * aspect_score + 1.0 * center_score

        return score, (x, y, w_box, h_box), area, aspect, center_dist

    def _trim_blue_margins(self, roi_bgr):
        if roi_bgr is None or roi_bgr.size == 0:
            return roi_bgr, (0, 0, 0, 0), False

        non_blue_mask, _ = self._make_non_blue_mask(roi_bgr)
        ys, xs = np.where(non_blue_mask > 0)
        if len(xs) == 0 or len(ys) == 0:
            return roi_bgr, (0, 0, roi_bgr.shape[1], roi_bgr.shape[0]), False

        x1, x2 = int(xs.min()), int(xs.max()) + 1
        y1, y2 = int(ys.min()), int(ys.max()) + 1

        h, w = roi_bgr.shape[:2]
        x1, y1, x2, y2 = self._clip_bbox(x1, y1, x2, y2, w, h)
        trimmed = roi_bgr[y1:y2, x1:x2].copy()
        return trimmed, (x1, y1, x2, y2), True

    def _shrink_inside(self, roi_bgr, shrink_px: int):
        if roi_bgr is None or roi_bgr.size == 0:
            return roi_bgr
        h, w = roi_bgr.shape[:2]
        s = int(max(0, shrink_px))
        if w <= 2 * s + 2 or h <= 2 * s + 2:
            return roi_bgr
        return roi_bgr[s:h - s, s:w - s].copy()

    def _blue_fraction(self, roi_bgr):
        if roi_bgr is None or roi_bgr.size == 0:
            return 1.0
        _, blue_mask = self._make_non_blue_mask(roi_bgr)
        return float(np.count_nonzero(blue_mask)) / float(blue_mask.size)

    def _score_fallback_contour(self, c, frame_w, frame_h):
        """Softer fallback scoring: choose the most useful non-blue contour even if it is not rectangular/centered."""
        area = float(cv2.contourArea(c))
        if area < max(float(ROI_FALLBACK_MIN_AREA), float(self.min_contour_area)):
            return None
        frame_area = float(frame_w * frame_h)
        if area > ROI_MAX_AREA_FRAC * frame_area:
            return None
        x, y, w_box, h_box = cv2.boundingRect(c)
        if w_box <= 1 or h_box <= 1:
            return None
        aspect = w_box / float(h_box)
        cx = x + w_box / 2.0
        cy = y + h_box / 2.0
        frame_cx = frame_w / 2.0
        frame_cy = frame_h / 2.0
        center_error_x = cx - frame_cx
        center_error_y = cy - frame_cy
        center_dist = (center_error_x ** 2 + center_error_y ** 2) ** 0.5
        aspect_score = 1.0 - min(abs(aspect - 1.0), 1.0)
        area_score = area / frame_area
        center_score = 1.0 - min(center_dist / max(min(frame_w, frame_h), 1.0), 1.0)
        border_penalty = 0.25 if self._is_border_touching(x, y, w_box, h_box, frame_w, frame_h) else 0.0
        score = 3.0 * area_score + 1.0 * aspect_score + 0.75 * center_score - border_penalty
        return score, (x, y, w_box, h_box), area, aspect, center_dist, center_error_x, center_error_y

    def _crop_trim_from_xywh(self, frame_bgr, x, y, w_box, h_box, frame_w, frame_h):
        """Crop tight non-blue ROI, trim blue edges, do not resize, do not force square; keep rectangular aspect."""
        x1 = x - self.padding
        y1 = y - self.padding
        x2 = x + w_box + self.padding
        y2 = y + h_box + self.padding
        x1, y1, x2, y2 = self._clip_bbox(x1, y1, x2, y2, frame_w, frame_h)
        roi_bgr = frame_bgr[y1:y2, x1:x2].copy()
        roi_bgr, local_trim_bbox, trim_ok = self._trim_blue_margins(roi_bgr)
        lx1, ly1, lx2, ly2 = local_trim_bbox
        x1 += lx1
        y1 += ly1
        x2 = x1 + roi_bgr.shape[1]
        y2 = y1 + roi_bgr.shape[0]
        base_shrink = int(ROI_EDGE_SHRINK_PX)
        roi_bgr = self._shrink_inside(roi_bgr, base_shrink)
        x1 += base_shrink
        y1 += base_shrink
        x2 = x1 + roi_bgr.shape[1]
        y2 = y1 + roi_bgr.shape[0]
        extra_shrink = 0
        while self._blue_fraction(roi_bgr) > 0.001 and extra_shrink < 8:
            roi_bgr = self._shrink_inside(roi_bgr, 1)
            x1 += 1
            y1 += 1
            x2 = x1 + roi_bgr.shape[1]
            y2 = y1 + roi_bgr.shape[0]
            extra_shrink += 1
        return roi_bgr, (int(x1), int(y1), int(x2), int(y2)), bool(trim_ok), int(extra_shrink)

    def detect(self, frame_bgr, debug_prefix: Optional[str] = None):
        if frame_bgr is None:
            return None, None, {"error": "frame is None", "roi_valid": False}

        frame_h, frame_w = frame_bgr.shape[:2]
        mask, blue_mask = self._make_non_blue_mask(frame_bgr)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            self._save_debug(debug_prefix, frame_bgr, mask, blue_mask, None, None)
            return None, None, {"error": "no contours found in non-blue mask", "roi_valid": False, "roi_fallback_used": False, "roi_failure_reason": "no_contour", "resolution": f"{frame_h}x{frame_w}"}

        strict_candidates = []
        fallback_candidates = []
        rejected_contours = 0

        for c in contours:
            scored = self._score_contour(c, frame_w, frame_h)
            if scored is None:
                rejected_contours += 1
            else:
                strict_candidates.append((scored, c))
            fallback_scored = self._score_fallback_contour(c, frame_w, frame_h)
            if fallback_scored is not None:
                fallback_candidates.append((fallback_scored, c))

        if strict_candidates:
            strict_candidates.sort(key=lambda item: item[0][0], reverse=True)
            (score, bbox_xywh, area, aspect, center_dist), best_contour = strict_candidates[0]
            x, y, w_box, h_box = bbox_xywh
            roi_valid = True
            roi_fallback_used = False
            roi_failure_reason = ""
            roi_status = "strict_rectangular_centered_roi_ok"
        else:
            if not ROI_USE_FALLBACK_BEST_AVAILABLE or not fallback_candidates:
                self._save_debug(debug_prefix, frame_bgr, mask, blue_mask, None, None)
                return None, None, {"error": "no valid rectangular centered non-blue contour", "roi_valid": False, "roi_fallback_used": False, "roi_failure_reason": "no_strict_rectangular_roi_and_no_fallback_contour", "num_contours": len(contours), "num_rejected_contours": rejected_contours, "resolution": f"{frame_h}x{frame_w}"}
            fallback_candidates.sort(key=lambda item: item[0][0], reverse=True)
            (score, bbox_xywh, area, aspect, center_dist, _cex, _cey), best_contour = fallback_candidates[0]
            x, y, w_box, h_box = bbox_xywh
            roi_valid = False
            roi_fallback_used = True
            roi_failure_reason = "no_strict_rectangular_centered_roi"
            roi_status = "fallback_best_available_non_rectangular_roi"

        cx = x + w_box / 2.0
        cy = y + h_box / 2.0
        center_error_x = cx - frame_w / 2.0
        center_error_y = cy - frame_h / 2.0
        border_touch = self._is_border_touching(x, y, w_box, h_box, frame_w, frame_h)

        roi_bgr, bbox, trim_ok, extra_shrink = self._crop_trim_from_xywh(frame_bgr, x, y, w_box, h_box, frame_w, frame_h)
        if roi_bgr is None or roi_bgr.size == 0:
            self._save_debug(debug_prefix, frame_bgr, mask, blue_mask, None, None)
            return None, None, {"error": "empty ROI after blue trimming", "roi_valid": False, "roi_fallback_used": bool(roi_fallback_used), "roi_failure_reason": "empty_after_trim"}

        bx1, by1, bx2, by2 = bbox
        vis = frame_bgr.copy()
        cv2.drawContours(vis, [best_contour], -1, (0, 255, 255), 2)
        cv2.rectangle(vis, (bx1, by1), (bx2, by2), (0, 255, 0), 2)
        final_blue_fraction = self._blue_fraction(roi_bgr)
        self._save_debug(debug_prefix, frame_bgr, mask, blue_mask, vis, roi_bgr)

        return roi_bgr, bbox, {"bbox": bbox, "roi_valid": bool(roi_valid), "roi_fallback_used": bool(roi_fallback_used), "roi_status": roi_status, "roi_failure_reason": roi_failure_reason, "merged_area": float(area), "roi_area": float((bx2 - bx1) * (by2 - by1)), "num_contours": len(contours), "num_valid_contours": len(strict_candidates), "num_fallback_contours": len(fallback_candidates), "num_rejected_contours": rejected_contours, "aspect": float(aspect), "center_dist_px": float(center_dist), "center_error_x": float(center_error_x), "center_error_y": float(center_error_y), "border_touch": bool(border_touch), "trim_ok": bool(trim_ok), "extra_shrink_px": int(extra_shrink), "final_blue_fraction": float(final_blue_fraction), "resolution": f"{frame_h}x{frame_w}"}

    def _save_debug(self, prefix, frame, mask, blue_mask, vis, roi):
        if self.debug_dir is None or not prefix:
            return

        cv2.imwrite(str(self.debug_dir / f"{prefix}_frame.png"), frame)
        cv2.imwrite(str(self.debug_dir / f"{prefix}_mask_non_blue.png"), mask)
        cv2.imwrite(str(self.debug_dir / f"{prefix}_mask_blue.png"), blue_mask)
        if vis is not None:
            cv2.imwrite(str(self.debug_dir / f"{prefix}_bbox.png"), vis)
        if roi is not None:
            cv2.imwrite(str(self.debug_dir / f"{prefix}_roi.png"), roi)


_roi_detector = None


def get_roi_detector():
    global _roi_detector
    if _roi_detector is None:
        _roi_detector = BackgroundROIDetector(
            min_area=ROI_MIN_AREA,
            min_contour_area=ROI_MIN_CONTOUR_AREA,
            morph_open_ksize=ROI_MORPH_OPEN_KSIZE,
            morph_close_ksize=ROI_MORPH_CLOSE_KSIZE,
            padding=ROI_PADDING,
            debug_dir=EDGE_ROI_DEBUG_DIR if SAVE_DEBUG_IMAGES else None,
        )
        print("[EDGE] ROI detector initialized for edge-side cropping")
    return _roi_detector

_model = None


def get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = YOLO(MODEL_PATH)
                print(f"[EDGE] Loaded model from: {MODEL_PATH}")
    return _model


def preload_model_for_steady_state():
    """
    Load YOLO and optionally run one synthetic inference before accepting
    experiment traffic. This keeps model loading and CUDA/kernel warm-up out of
    the first logged object latency.
    """
    if not EDGE_PRELOAD_MODEL:
        print("[EDGE] Model preload disabled by EDGE_PRELOAD_MODEL=0")
        return

    t0 = time.perf_counter_ns()
    model = get_model()
    load_ms = (time.perf_counter_ns() - t0) / 1e6
    print(f"[EDGE] Model preload completed in {load_ms:.2f} ms")

    if not EDGE_WARMUP_INFERENCE:
        print("[EDGE] Warm-up inference disabled by EDGE_WARMUP_INFERENCE=0")
        return

    warmup_img = np.zeros(
        (EDGE_WARMUP_IMAGE_HEIGHT, EDGE_WARMUP_IMAGE_WIDTH, 3),
        dtype=np.uint8,
    )
    t1 = time.perf_counter_ns()
    with _inference_sem:
        model.predict(
            source=warmup_img,
            conf=CONF_THRESHOLD,
            iou=IOU_THRESHOLD,
            verbose=False,
            save=False,
        )
    warmup_ms = (time.perf_counter_ns() - t1) / 1e6
    print(
        "[EDGE] Warm-up inference completed "
        f"on {EDGE_WARMUP_IMAGE_WIDTH}x{EDGE_WARMUP_IMAGE_HEIGHT} dummy image "
        f"in {warmup_ms:.2f} ms"
    )


def maybe_crop(img: np.ndarray) -> np.ndarray:
    if not USE_ROI:
        return img
    x1, y1, x2, y2 = ROI
    return img[y1:y2, x1:x2].copy()


def rotate_image_for_inference(image: np.ndarray) -> np.ndarray:
    """
    Rotate the ROI image before YOLO inference.

    The edge pipeline uses OpenCV images, so the image is a BGR NumPy array.
    Your PIL setting rotate(-90, expand=True) corresponds to "cw90" here.
    """
    if image is None or image.size == 0:
        return image

    if not ROTATE_ROI_BEFORE_INFERENCE or ROI_ROTATION_MODE == "none":
        return image

    if ROI_ROTATION_MODE == "cw90":
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)

    if ROI_ROTATION_MODE == "ccw90":
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)

    if ROI_ROTATION_MODE == "180":
        return cv2.rotate(image, cv2.ROTATE_180)

    raise ValueError(f"Unsupported ROI_ROTATION_MODE={ROI_ROTATION_MODE}")


def send_json_line(conn: socket.socket, payload: dict):
    conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))


def recv_line_and_rest(conn: socket.socket):
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(65536)
        if not chunk:
            return None, b""
        buf += chunk
    line, _, rest = buf.partition(b"\n")
    return json.loads(line.decode("utf-8")), rest


def recv_exact(conn: socket.socket, n: int, initial=b"") -> bytes:
    buf = initial
    while len(buf) < n:
        chunk = conn.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError("Socket closed while receiving fixed-size payload")
        buf += chunk
    return buf[:n]


def decode_image_bytes(payload_bytes: bytes, image_encoding: str, width: int, height: int, channels: int) -> np.ndarray:
    if image_encoding == "raw":
        arr = np.frombuffer(payload_bytes, dtype=np.uint8)
        expected = width * height * channels
        if arr.size != expected:
            raise ValueError(f"RAW payload size mismatch: got {arr.size}, expected {expected}")
        if channels == 1:
            img = arr.reshape((height, width))
        else:
            img = arr.reshape((height, width, channels))
        return img.copy()

    arr = np.frombuffer(payload_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Failed to decode image encoding={image_encoding}")
    return img


def init_edge_csv():
    if not WRITE_EDGE_CSV:
        return
    EDGE_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not EDGE_CSV_PATH.exists():
        with EDGE_CSV_PATH.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp",
                "obj_id",
                "point_id",
                "part_name",
                "expected_label",
                "predicted_class",
                "predicted_confidence",
                "correct",
                "decision",
                "num_detections",
                "bbox",
                "image_width",
                "image_height",
                "image_channels",
                "raw_bytes",
                "payload_bytes",
                "image_encoding",
                "inference_ms",
                "edge_decode_ms",
                "edge_process_ms",
                "edge_total_ms",
                "edge_label",
                "edge_cpus",
                "edge_compute_capacity",
                "edge_resource_control",
                "edge_torch_threads",
                "edge_roi_found",
                "edge_roi_valid",
                "edge_roi_fallback_used",
                "edge_roi_status",
                "edge_roi_failure_reason",
                "edge_roi_bbox",
                "edge_roi_width",
                "edge_roi_height",
                "edge_roi_aspect",
                "edge_roi_area",
                "edge_roi_center_error_x",
                "edge_roi_center_error_y",
                "edge_roi_blue_fraction",
                "edge_roi_border_touch",
                "roi_rotated_before_inference",
                "roi_rotation_mode",
                "inference_image_width",
                "inference_image_height",
            ])


def append_edge_csv_row(row: dict):
    if not WRITE_EDGE_CSV:
        return
    init_edge_csv()
    with EDGE_CSV_PATH.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        writer.writerow(row)


def save_debug_image(obj_id: str, point_id: int, part_name: str, image: np.ndarray, detections):
    if not SAVE_DEBUG_IMAGES:
        return

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    dbg = image.copy()

    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        cls_name = det["class_name"]
        conf = det["confidence"]

        color = (0, 0, 255)
        cv2.rectangle(dbg, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            dbg,
            f"{cls_name}:{conf:.2f}",
            (x1, max(20, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
            cv2.LINE_AA,
        )

    # If healthy, write that on the image for clarity
    if len(detections) == 0:
        cv2.putText(
            dbg,
            "healthy (no defect detected)",
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )

    safe_part = str(part_name).replace("/", "_").replace(" ", "_")
    out_path = DEBUG_DIR / f"point_{point_id}__{safe_part}__{obj_id}.png"
    cv2.imwrite(str(out_path), dbg)


def inspect_neu_bgr(
    bgr_img: np.ndarray,
    obj_id: str,
    point_id: int,
    part_name: str,
    expected_label: str,
) -> Dict[str, Any]:
    """
    Binary decision rule:
      - any detected defect box => defective
      - no detected boxes => healthy
    """
    model = get_model()

    # Edge-side ROI cropping. Factory sends the full frame; the edge extracts
    # the part-only crop before YOLO. If ROI extraction fails here, we return
    # an explicit edge ROI failure instead of mixing bad crops into accuracy.
    edge_roi_found = False
    edge_roi_valid = False
    edge_roi_fallback_used = False
    edge_roi_status = "not_attempted"
    edge_roi_failure_reason = ""
    edge_roi_bbox = []
    edge_roi_aspect = ""
    edge_roi_area = ""
    edge_roi_center_error_x = ""
    edge_roi_center_error_y = ""
    edge_roi_blue_fraction = ""
    edge_roi_border_touch = ""
    edge_roi_width = ""
    edge_roi_height = ""

    if USE_BACKGROUND_ROI:
        debug_prefix = f"{obj_id}_p{point_id}_{int(time.time() * 1000)}"
        roi_img, roi_bbox, roi_debug = get_roi_detector().detect(
            bgr_img,
            debug_prefix=debug_prefix,
        )

        if roi_img is None or roi_bbox is None:
            return {
                "decision": "unknown",
                "combined_score": 0.0,
                "predicted_class": "",
                "predicted_confidence": 0.0,
                "bbox": [],
                "num_detections": 0,
                "inference_ms": 0.0,
                "correct": "",
                "status": "edge_roi_failed",
                "part_name": part_name,
                "edge_roi_found": False,
                "edge_roi_valid": False,
                "edge_roi_fallback_used": False,
                "edge_roi_status": roi_debug.get("error", "edge_roi_failed"),
                "edge_roi_failure_reason": roi_debug.get("roi_failure_reason", roi_debug.get("error", "")),
                "edge_roi_bbox": [],
                "edge_roi_width": "",
                "edge_roi_height": "",
                "edge_roi_aspect": "",
                "edge_roi_area": "",
                "edge_roi_center_error_x": "",
                "edge_roi_center_error_y": "",
                "edge_roi_blue_fraction": "",
                "edge_roi_border_touch": "",
                "roi_rotated_before_inference": False,
                "roi_rotation_mode": "",
                "inference_image_width": "",
                "inference_image_height": "",
            }

        image = roi_img
        edge_roi_bbox = list(roi_bbox)
        edge_roi_found = True
        edge_roi_valid = bool(roi_debug.get("roi_valid", True))
        edge_roi_fallback_used = bool(roi_debug.get("roi_fallback_used", False))
        edge_roi_status = roi_debug.get("roi_status", "edge_roi_ok")
        edge_roi_failure_reason = roi_debug.get("roi_failure_reason", "")
        edge_roi_aspect = roi_debug.get("aspect", "")
        edge_roi_area = roi_debug.get("roi_area", roi_debug.get("merged_area", ""))
        edge_roi_center_error_x = roi_debug.get("center_error_x", "")
        edge_roi_center_error_y = roi_debug.get("center_error_y", "")
        edge_roi_blue_fraction = roi_debug.get("final_blue_fraction", "")
        edge_roi_border_touch = roi_debug.get("border_touch", "")
        edge_roi_height, edge_roi_width = image.shape[:2]
    else:
        image = maybe_crop(bgr_img)
        edge_roi_status = "edge_roi_disabled"
        edge_roi_height, edge_roi_width = image.shape[:2]

    # ------------------------------------------------
    # Rotate ROI before YOLO inference
    # ------------------------------------------------
    roi_rotated_before_inference = bool(
        ROTATE_ROI_BEFORE_INFERENCE and ROI_ROTATION_MODE != "none"
    )
    roi_rotation_mode = ROI_ROTATION_MODE if roi_rotated_before_inference else "none"

    image = rotate_image_for_inference(image)
    inference_image_height, inference_image_width = image.shape[:2]

    if roi_rotated_before_inference:
        edge_roi_status = f"{edge_roi_status}_rotated_{ROI_ROTATION_MODE}"

    t0 = time.perf_counter_ns()
    # Limit concurrent YOLO calls to avoid overloading the GPU/CPU.
    with _inference_sem:
        results = model.predict(
            source=image,
            conf=CONF_THRESHOLD,
            iou=IOU_THRESHOLD,
            verbose=False,
            save=False,
        )
    t1 = time.perf_counter_ns()
    inference_ms = (t1 - t0) / 1e6

    result = results[0]
    detections = []

    if result.boxes is not None:
        for box in result.boxes:
            cls_id = int(box.cls[0].item())
            conf = float(box.conf[0].item())
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            detections.append({
                "class_id": cls_id,
                "class_name": model.names[cls_id],
                "confidence": conf,
                "bbox": [x1, y1, x2, y2],
            })

    if detections:
        detections.sort(key=lambda d: d["confidence"], reverse=True)
        best = detections[0]
        decision = "defective"
        predicted_class = best["class_name"]
        predicted_confidence = best["confidence"]
        bbox = best["bbox"]
    else:
        decision = "healthy"
        predicted_class = ""
        predicted_confidence = 0.0
        bbox = []

    #correct = int(expected_label == predicted_class) if expected_label else ""
    def norm_label(x):
        return str(x).strip().lower().replace("-", "_")

    correct = int(norm_label(expected_label) == norm_label(predicted_class)) if expected_label else ""

    save_debug_image(
        obj_id=obj_id,
        point_id=point_id,
        part_name=part_name,
        image=image,
        detections=detections,
    )

    return {
        "decision": decision,
        "combined_score": float(predicted_confidence),
        "predicted_class": predicted_class,
        "predicted_confidence": float(predicted_confidence),
        "bbox": bbox,
        "num_detections": len(detections),
        "inference_ms": inference_ms,
        "correct": correct,
        "status": "ok",
        "part_name": part_name,
        "edge_roi_found": edge_roi_found,
        "edge_roi_valid": edge_roi_valid,
        "edge_roi_fallback_used": edge_roi_fallback_used,
        "edge_roi_status": edge_roi_status,
        "edge_roi_failure_reason": edge_roi_failure_reason,
        "edge_roi_bbox": edge_roi_bbox,
        "edge_roi_width": edge_roi_width,
        "edge_roi_height": edge_roi_height,
        "edge_roi_aspect": edge_roi_aspect,
        "edge_roi_area": edge_roi_area,
        "edge_roi_center_error_x": edge_roi_center_error_x,
        "edge_roi_center_error_y": edge_roi_center_error_y,
        "edge_roi_blue_fraction": edge_roi_blue_fraction,
        "edge_roi_border_touch": edge_roi_border_touch,
        "roi_rotated_before_inference": roi_rotated_before_inference,
        "roi_rotation_mode": roi_rotation_mode,
        "inference_image_width": inference_image_width,
        "inference_image_height": inference_image_height,
    }


class EdgeRelayServer:
    def __init__(self, listen_ip: str = LISTEN_IP, listen_port: int = LISTEN_PORT):
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        init_edge_csv()

    def handle_connection(self, conn: socket.socket, addr):
        print(f"[EDGE] Client connected from {addr}")
        conn.settimeout(SOCKET_TIMEOUT_SEC)

        try:
            while True:
                t_req_recv_ns = time.perf_counter_ns()
                msg, rest = recv_line_and_rest(conn)
                if msg is None:
                    print("[EDGE] Client disconnected")
                    break

                print(f"[EDGE] Received message type={msg.get('type')}")

                if msg.get("type") != "request":
                    send_json_line(conn, {"type": "error", "payload": {"error": "unexpected_message_type"}})
                    continue

                payload = msg.get("payload", {})
                image_len = int(msg.get("image_len", 0))

                obj_id = payload.get("obj_id", "unknown_obj")
                point_id = int(payload.get("point_id", -1))
                part_name = payload.get("part_name", "unknown_part")
                expected_label = payload.get("label", "").strip()

                image_width = int(payload.get("image_width", 0))
                image_height = int(payload.get("image_height", 0))
                image_channels = int(payload.get("image_channels", 3))
                image_encoding = payload.get("image_encoding", "jpeg")
                raw_bytes = payload.get("raw_bytes", "")
                payload_bytes = payload.get("payload_bytes", "")

                image_bytes = recv_exact(conn, image_len, initial=rest)

                t_ack_send_ns = time.perf_counter_ns()
                t_ack_send_wall_ns = time.time_ns()
                edge_image_receive_ms = (t_ack_send_ns - t_req_recv_ns) / 1e6
                send_json_line(
                    conn,
                    {
                        "type": "ack",
                        "payload": {
                            "obj_id": obj_id,
                            "point_id": point_id,
                            "status": "received",
                            "edge_ack_ms": edge_image_receive_ms,
                            "edge_image_receive_ms": edge_image_receive_ms,
                            "edge_receive_complete_wall_ns": t_ack_send_wall_ns,
                            "edge_ack_send_wall_ns": t_ack_send_wall_ns,
                            "edge_ack_send_perf_ns": t_ack_send_ns,
                        },
                    },
                )
                print(f"[EDGE] ACK sent for obj={obj_id} point={point_id}")

                edge_decode_ms = 0.0
                edge_process_ms = 0.0
                edge_total_ms = 0.0

                try:
                    t_decode_start_ns = time.perf_counter_ns()
                    t_decode_start_wall_ns = time.time_ns()
                    bgr_img = decode_image_bytes(
                        payload_bytes=image_bytes,
                        image_encoding=image_encoding,
                        width=image_width,
                        height=image_height,
                        channels=image_channels,
                    )
                    t_decode_end_ns = time.perf_counter_ns()
                    t_decode_end_wall_ns = time.time_ns()
                    edge_decode_ms = (t_decode_end_ns - t_decode_start_ns) / 1e6

                    t_process_start_ns = time.perf_counter_ns()
                    t_process_start_wall_ns = time.time_ns()
                    inspection = inspect_neu_bgr(
                        bgr_img=bgr_img,
                        obj_id=obj_id,
                        point_id=point_id,
                        part_name=part_name,
                        expected_label=expected_label,
                    )
                    t_process_end_ns = time.perf_counter_ns()
                    t_process_end_wall_ns = time.time_ns()
                    edge_process_ms = (t_process_end_ns - t_process_start_ns) / 1e6
                    edge_total_ms = edge_decode_ms + edge_process_ms

                    result_payload = {
                        "obj_id": obj_id,
                        "point_id": point_id,
                        "decision": inspection["decision"],
                        "combined_score": inspection["combined_score"],
                        "part_name": inspection["part_name"],
                        "status": inspection["status"],
                        "predicted_class": inspection["predicted_class"],
                        "predicted_confidence": inspection["predicted_confidence"],
                        "bbox": inspection["bbox"],
                        "num_detections": inspection["num_detections"],
                        "inference_ms": inspection["inference_ms"],
                        "edge_decode_ms": edge_decode_ms,
                        "edge_process_ms": edge_process_ms,
                        "edge_total_ms": edge_total_ms,
                        "edge_label": EDGE_LABEL,
                        "edge_cpus": EDGE_CPUS,
                        "edge_compute_capacity": EDGE_COMPUTE_CAPACITY,
                        "edge_resource_control": EDGE_RESOURCE_CONTROL,
                        "edge_torch_threads": EDGE_TORCH_THREADS,
                        "edge_image_receive_ms": edge_image_receive_ms,
                        "edge_receive_complete_wall_ns": t_ack_send_wall_ns,
                        "edge_decode_start_wall_ns": t_decode_start_wall_ns,
                        "edge_decode_end_wall_ns": t_decode_end_wall_ns,
                        "edge_process_start_wall_ns": t_process_start_wall_ns,
                        "edge_process_end_wall_ns": t_process_end_wall_ns,
                        "edge_roi_found": inspection.get("edge_roi_found", ""),
                        "edge_roi_valid": inspection.get("edge_roi_valid", ""),
                        "edge_roi_fallback_used": inspection.get("edge_roi_fallback_used", ""),
                        "edge_roi_status": inspection.get("edge_roi_status", ""),
                        "edge_roi_failure_reason": inspection.get("edge_roi_failure_reason", ""),
                        "edge_roi_bbox": inspection.get("edge_roi_bbox", []),
                        "edge_roi_width": inspection.get("edge_roi_width", ""),
                        "edge_roi_height": inspection.get("edge_roi_height", ""),
                        "edge_roi_aspect": inspection.get("edge_roi_aspect", ""),
                        "edge_roi_area": inspection.get("edge_roi_area", ""),
                        "edge_roi_center_error_x": inspection.get("edge_roi_center_error_x", ""),
                        "edge_roi_center_error_y": inspection.get("edge_roi_center_error_y", ""),
                        "edge_roi_blue_fraction": inspection.get("edge_roi_blue_fraction", ""),
                        "edge_roi_border_touch": inspection.get("edge_roi_border_touch", ""),
                        "roi_rotated_before_inference": inspection.get("roi_rotated_before_inference", ""),
                        "roi_rotation_mode": inspection.get("roi_rotation_mode", ""),
                        "inference_image_width": inspection.get("inference_image_width", ""),
                        "inference_image_height": inspection.get("inference_image_height", ""),
                    }

                    append_edge_csv_row({
                        "timestamp": time.time(),
                        "obj_id": obj_id,
                        "point_id": point_id,
                        "part_name": part_name,
                        "expected_label": expected_label,
                        "predicted_class": inspection["predicted_class"],
                        "predicted_confidence": inspection["predicted_confidence"],
                        "correct": inspection["correct"],
                        "decision": inspection["decision"],
                        "num_detections": inspection["num_detections"],
                        "bbox": json.dumps(inspection["bbox"]),
                        "image_width": image_width,
                        "image_height": image_height,
                        "image_channels": image_channels,
                        "raw_bytes": raw_bytes,
                        "payload_bytes": payload_bytes,
                        "image_encoding": image_encoding,
                        "inference_ms": inspection["inference_ms"],
                        "edge_decode_ms": edge_decode_ms,
                        "edge_process_ms": edge_process_ms,
                        "edge_total_ms": edge_total_ms,
                        "edge_label": EDGE_LABEL,
                        "edge_cpus": EDGE_CPUS,
                        "edge_compute_capacity": EDGE_COMPUTE_CAPACITY,
                        "edge_resource_control": EDGE_RESOURCE_CONTROL,
                        "edge_torch_threads": EDGE_TORCH_THREADS,
                        "edge_roi_found": inspection.get("edge_roi_found", ""),
                        "edge_roi_valid": inspection.get("edge_roi_valid", ""),
                        "edge_roi_fallback_used": inspection.get("edge_roi_fallback_used", ""),
                        "edge_roi_status": inspection.get("edge_roi_status", ""),
                        "edge_roi_failure_reason": inspection.get("edge_roi_failure_reason", ""),
                        "edge_roi_bbox": json.dumps(inspection.get("edge_roi_bbox", [])),
                        "edge_roi_width": inspection.get("edge_roi_width", ""),
                        "edge_roi_height": inspection.get("edge_roi_height", ""),
                        "edge_roi_aspect": inspection.get("edge_roi_aspect", ""),
                        "edge_roi_area": inspection.get("edge_roi_area", ""),
                        "edge_roi_center_error_x": inspection.get("edge_roi_center_error_x", ""),
                        "edge_roi_center_error_y": inspection.get("edge_roi_center_error_y", ""),
                        "edge_roi_blue_fraction": inspection.get("edge_roi_blue_fraction", ""),
                        "edge_roi_border_touch": inspection.get("edge_roi_border_touch", ""),
                        "roi_rotated_before_inference": inspection.get("roi_rotated_before_inference", ""),
                        "roi_rotation_mode": inspection.get("roi_rotation_mode", ""),
                        "inference_image_width": inspection.get("inference_image_width", ""),
                        "inference_image_height": inspection.get("inference_image_height", ""),
                    })

                except Exception as e:
                    result_payload = {
                        "obj_id": obj_id,
                        "point_id": point_id,
                        "decision": "unknown",
                        "combined_score": 0.0,
                        "part_name": part_name,
                        "status": f"edge_error: {e}",
                        "predicted_class": "",
                        "predicted_confidence": 0.0,
                        "bbox": [],
                        "num_detections": 0,
                        "inference_ms": 0.0,
                        "edge_decode_ms": edge_decode_ms,
                        "edge_process_ms": edge_process_ms,
                        "edge_total_ms": edge_total_ms,
                        "edge_label": EDGE_LABEL,
                        "edge_cpus": EDGE_CPUS,
                        "edge_compute_capacity": EDGE_COMPUTE_CAPACITY,
                        "edge_resource_control": EDGE_RESOURCE_CONTROL,
                        "edge_torch_threads": EDGE_TORCH_THREADS,
                        "edge_roi_found": "",
                        "edge_roi_valid": "",
                        "edge_roi_fallback_used": "",
                        "edge_roi_status": "",
                        "edge_roi_failure_reason": "",
                        "edge_roi_bbox": [],
                        "edge_roi_width": "",
                        "edge_roi_height": "",
                        "edge_roi_aspect": "",
                        "edge_roi_area": "",
                        "edge_roi_center_error_x": "",
                        "edge_roi_center_error_y": "",
                        "edge_roi_blue_fraction": "",
                        "edge_roi_border_touch": "",
                        "roi_rotated_before_inference": "",
                        "roi_rotation_mode": "",
                        "inference_image_width": "",
                        "inference_image_height": "",
                    }

                # send_json_line(conn, {"type": "result", "payload": result_payload})
                # print(f"[EDGE] Result sent: {result_payload}")
                # Timestamp immediately before sending the small result/command packet.
                # This is the start of the URLLC-like result-feedback path.
                result_payload["edge_result_send_wall_ns"] = time.time_ns()
                result_payload["edge_result_send_perf_ns"] = time.perf_counter_ns()

                send_json_line(conn, {"type": "result", "payload": result_payload})
                print(f"[EDGE] Result sent: {result_payload}")

        except socket.timeout:
            print("[EDGE] Connection timed out")
        except Exception as e:
            print(f"[EDGE] Connection handling failed: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def serve_forever(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.listen_ip, self.listen_port))
        server.listen(5)

        print(
            f"[EDGE] Listening on {self.listen_ip}:{self.listen_port} | "
            f"max_connection_threads={MAX_EDGE_CONNECTION_THREADS} "
            f"max_yolo_workers={MAX_EDGE_YOLO_WORKERS}"
        )

        connection_sem = threading.Semaphore(MAX_EDGE_CONNECTION_THREADS)

        def worker(c, a):
            try:
                self.handle_connection(c, a)
            finally:
                connection_sem.release()

        while True:
            conn, addr = server.accept()

            if not connection_sem.acquire(blocking=False):
                print(f"[EDGE] Busy: rejecting connection from {addr}")
                try:
                    send_json_line(
                        conn,
                        {
                            "type": "error",
                            "payload": {
                                "status": "edge_connection_limit_reached",
                                "max_connection_threads": MAX_EDGE_CONNECTION_THREADS,
                            },
                        },
                    )
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
                continue

            threading.Thread(
                target=worker,
                args=(conn, addr),
                daemon=True,
            ).start()


def main():
    print(
        f"[EDGE-OAI-DEV] Starting edge relay on {LISTEN_IP}:{LISTEN_PORT} "
        f"with MODEL_PATH={MODEL_PATH} "
        f"EDGE_LABEL={EDGE_LABEL} "
        f"EDGE_CPUS={EDGE_CPUS} "
        f"EDGE_COMPUTE_CAPACITY={EDGE_COMPUTE_CAPACITY} "
        f"EDGE_RESOURCE_CONTROL={EDGE_RESOURCE_CONTROL} "
        f"EDGE_TORCH_THREADS={EDGE_TORCH_THREADS}"
    )
    preload_model_for_steady_state()
    EdgeRelayServer().serve_forever()


if __name__ == "__main__":
    main()
