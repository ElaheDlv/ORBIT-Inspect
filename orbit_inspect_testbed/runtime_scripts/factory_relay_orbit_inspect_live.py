import csv
import json
import os
import socket
import time
import threading
from pathlib import Path
from typing import Optional, Tuple
from collections import deque

import cv2
import numpy as np
import rclpy
from cv_bridge import CvBridge
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import String

REQUEST_TOPIC = "/fortuna_inspection/request"
RESULT_TOPIC = "/fortuna_inspection/result"
ACK_TOPIC = "/fortuna_inspection/ack"
CONTROL_TOPIC = "/orbit_inspect/control"
CONTROL_ACK_TOPIC = "/orbit_inspect/control_ack"
CAMERA_TOPIC = "/new_cam_01/rgb"

UE_FORWARDER_IP = os.getenv("UE_FORWARDER_IP", "127.0.0.1")
UE_FORWARDER_PORT = int(os.getenv("UE_FORWARDER_PORT", "9200"))
SOCKET_TIMEOUT_SEC = float(os.getenv("SOCKET_TIMEOUT_SEC", "120.0"))

# ------------------------------------------------
# Factory concurrency limit
# ------------------------------------------------
MAX_FACTORY_IN_FLIGHT = 2

# ------------------------------------------------
# Transport/image config
# ------------------------------------------------
# ENCODING = "raw"   # "raw", "png", "jpeg"
# JPEG_QUALITY = 35

ENCODING = os.getenv("ENCODING", "raw")   # "raw", "png", "jpeg"
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "35"))
LIVE_CONTROL_STATE = {
    "run_id": "",
    "speed": "",
    "resolution": "",
    "prb": "",
    "cpu": "",
    "updated_at": "",
}
TESTBED_DIR = Path(__file__).resolve().parents[1]
LIVE_CONFIG_PATH = Path(os.getenv(
    "ORBIT_INSPECT_LIVE_CONFIG",
    str(TESTBED_DIR / "live_results" / "latest" / "current_live_config.json"),
))

# MODEL_PATH = "best_n.pt"
# model_name = Path(MODEL_PATH).stem

# RUN_SPEED = 1.0


# Used mainly for naming/logging consistency on the factory side.
# The actual YOLO model is loaded on the edge side.
MODEL_PATH = os.getenv("MODEL_PATH", "best_n.pt")
model_name = Path(MODEL_PATH).stem

# Used mainly for naming/logging consistency on the factory side.
# The actual conveyor speed is controlled inside Isaac.
RUN_SPEED = float(os.getenv("RUN_SPEED", "1.0"))

WRITE_LATENCY_CSV = True
LATENCY_CSV_PATH = Path(os.getenv("LATENCY_CSV_PATH", f"./latency_results_factory_oai_dev_multi_{model_name}_{RUN_SPEED}.csv"))

# ------------------------------------------------
# Factory-side visibility check via blue-conveyor color segmentation
# ------------------------------------------------
# IMPORTANT:
# The factory no longer sends cropped ROI images.
# This detector is used only to decide whether the part is fully visible.
# If the part is partial/not visible, the factory publishes a replay request
# and does not send the image to the edge.
USE_BACKGROUND_ROI = os.getenv("ORBIT_INSPECT_FACTORY_USE_ROI", "1").lower() not in {"0", "false", "no"}
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
ROI_DEBUG_DIR = Path(os.getenv("ROI_DEBUG_DIR", f"./factory_roi_debug_oai_dev_{model_name}_{RUN_SPEED}"))

# ------------------------------------------------
# Optional fixed crop (old behavior)
# ------------------------------------------------
USE_ROI = False
ROI = (517, 220, 795, 497)  # x1, y1, x2, y2

# # ------------------------------------------------
# # Moving-capture frame selection
# # ------------------------------------------------
# RECENT_FRAME_MAX_AGE_MS = 50.0
# REQUEST_IMAGE_TIMEOUT_SEC = 0.30

# # Controlled internal frame history.
# # ROS subscriber queue should stay shallow, while this buffer lets us deliberately
# # select a recent frame close to the inspection request time.
# FRAME_BUFFER_SIZE = 10
# MAX_PAST_FRAME_AGE_MS = 50.0
# MAX_FUTURE_FRAME_WAIT_SEC = 0.05


RECENT_FRAME_MAX_AGE_MS = 350.0
REQUEST_IMAGE_TIMEOUT_SEC = 0.40
FRAME_BUFFER_SIZE = 15
MAX_PAST_FRAME_AGE_MS = 200.0
MAX_FUTURE_FRAME_WAIT_SEC = 0.15


def maybe_crop(img):
    if not USE_ROI:
        return img
    x1, y1, x2, y2 = ROI
    return img[y1:y2, x1:x2].copy()


def send_json_line(sock: socket.socket, payload: dict):
    sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))


# def recv_json_line(sock: socket.socket) -> Optional[dict]:
#     buf = b""
#     while b"\n" not in buf:
#         chunk = sock.recv(65536)
#         if not chunk:
#             return None
#         buf += chunk
#     line, _, _rest = buf.partition(b"\n")
#     return json.loads(line.decode("utf-8"))

class JsonLineReader:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buf = b""

    def recv_json(self) -> Optional[dict]:
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                return None
            self.buf += chunk

        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line.decode("utf-8"))


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


class FactoryRelay(Node):
    def __init__(self):
        super().__init__("factory_relay")

        self.bridge = CvBridge()
        self.inflight_sem = threading.Semaphore(MAX_FACTORY_IN_FLIGHT)

        # # Shared camera-frame state. The camera callback writes these values,
        # # while request-processing threads read them, so protect them with a lock.
        # self.image_lock = threading.Lock()
        # self.latest_bgr = None
        # self.latest_stamp = None

        # # Optional diagnostic: used to warn when Isaac/ROS camera publishing slows down.
        # self.prev_image_stamp = None
        
        
        self.image_lock = threading.Lock()
        self.latest_bgr = None
        self.latest_stamp = None

        # Controlled history of recent received camera frames.
        # This is different from the ROS subscriber queue: we explicitly choose
        # the best frame from this buffer at request time.
        self.frame_buffer = deque(maxlen=FRAME_BUFFER_SIZE)

        # Debug timing variables
        self.prev_image_stamp = None
        self.image_count = 0
        self.request_count = 0



        self.request_cb_group = MutuallyExclusiveCallbackGroup()
        self.image_cb_group = ReentrantCallbackGroup()

        self.result_pub = self.create_publisher(String, RESULT_TOPIC, 10)
        self.ack_pub = self.create_publisher(String, ACK_TOPIC, 10)
        self.control_ack_pub = self.create_publisher(String, CONTROL_ACK_TOPIC, 10)

        self.req_sub = self.create_subscription(
            String,
            REQUEST_TOPIC,
            self.on_request,
            10,
            callback_group=self.request_cb_group,
        )
        self.control_sub = self.create_subscription(
            String,
            CONTROL_TOPIC,
            self.on_control,
            10,
            callback_group=self.request_cb_group,
        )
        image_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
        )

        self.img_sub = self.create_subscription(
            Image,
            CAMERA_TOPIC,
            self.on_image,
            image_qos,
            callback_group=self.image_cb_group,
        )

        self.roi_detector = None
        if USE_BACKGROUND_ROI:
            try:
                self.roi_detector = BackgroundROIDetector(
                    min_area=ROI_MIN_AREA,
                    min_contour_area=ROI_MIN_CONTOUR_AREA,
                    morph_open_ksize=ROI_MORPH_OPEN_KSIZE,
                    morph_close_ksize=ROI_MORPH_CLOSE_KSIZE,
                    padding=ROI_PADDING,
                    debug_dir=ROI_DEBUG_DIR if SAVE_ROI_DEBUG else None,
                )
                self.get_logger().info(
                    f"Blue tight part-only ROI enabled | no resize | fallback={ROI_FALLBACK_TO_FULL_FRAME}"
                )
            except Exception as e:
                self.get_logger().error(f"Failed to initialize ROI detector: {e}")
                self.roi_detector = None

        self.get_logger().info(
            f"Factory relay OAI-dev started | UE forwarder={UE_FORWARDER_IP}:{UE_FORWARDER_PORT} | encoding={ENCODING} jpeg_quality={JPEG_QUALITY} "
            f"camera_resolution_controlled_by_isaac "
            f"recent_frame_max_age_ms={RECENT_FRAME_MAX_AGE_MS} "
            f"max_factory_in_flight={MAX_FACTORY_IN_FLIGHT}"
        )

    def on_control(self, msg: String):
        global LIVE_CONTROL_STATE, LIVE_CONFIG_PATH

        ack = {
            "component": "factory_relay",
            "status": "applied",
            "received_at": time.time(),
            "pid": os.getpid(),
        }
        try:
            data = json.loads(msg.data)
            ack["command"] = data
            if data.get("run_id"):
                LIVE_CONTROL_STATE["run_id"] = str(data["run_id"])
            if data.get("config_path"):
                LIVE_CONFIG_PATH = Path(str(data["config_path"]))
                ack["config_path"] = str(LIVE_CONFIG_PATH)
            if "speed" in data:
                LIVE_CONTROL_STATE["speed"] = str(data["speed"])
            if "prb" in data:
                LIVE_CONTROL_STATE["prb"] = str(data["prb"])
            if "cpu" in data:
                LIVE_CONTROL_STATE["cpu"] = str(data["cpu"])
            LIVE_CONTROL_STATE["updated_at"] = str(time.time())
            resolution = data.get("resolution")
            if resolution:
                width_str, height_str = str(resolution).lower().split("x", 1)
                width = int(width_str)
                height = int(height_str)
                if width < 160 or height < 90 or width > 1920 or height > 1080:
                    raise ValueError(f"resolution out of supported range: {resolution}")
                LIVE_CONTROL_STATE["resolution"] = f"{width}x{height}"
                ack["resolution"] = f"{width}x{height}"
                self.get_logger().info(
                    f"[LiveControl] Recorded requested camera resolution {width}x{height} pid={os.getpid()}"
                )
        except Exception as e:
            ack["status"] = "error"
            ack["error"] = str(e)
            self.get_logger().error(f"[LiveControl] Failed to apply control message: {e}")
        finally:
            ack_msg = String()
            ack_msg.data = json.dumps(ack)
            self.control_ack_pub.publish(ack_msg)

    def refresh_live_control_state_from_config(self):
        global LATENCY_CSV_PATH
        if not LIVE_CONFIG_PATH.exists():
            return
        try:
            cfg = json.loads(LIVE_CONFIG_PATH.read_text())
        except Exception as e:
            self.get_logger().warning(f"[LiveConfig] Failed to read {LIVE_CONFIG_PATH}: {e}")
            return

        if cfg.get("run_id"):
            LIVE_CONTROL_STATE["run_id"] = str(cfg["run_id"])
        if "run_speed" in cfg:
            LIVE_CONTROL_STATE["speed"] = str(cfg["run_speed"])
        if "camera_width" in cfg and "camera_height" in cfg:
            LIVE_CONTROL_STATE["resolution"] = f"{int(cfg['camera_width'])}x{int(cfg['camera_height'])}"
        if "slice_ul_pct" in cfg:
            LIVE_CONTROL_STATE["prb"] = str(cfg["slice_ul_pct"])
        if "edge_cpus" in cfg:
            LIVE_CONTROL_STATE["cpu"] = str(cfg["edge_cpus"])
        if cfg.get("latency_csv_path"):
            next_path = Path(str(cfg["latency_csv_path"]))
            if next_path != LATENCY_CSV_PATH:
                self.get_logger().info(f"[LiveConfig] Switching latency CSV to {next_path}")
                LATENCY_CSV_PATH = next_path

    # def on_image(self, msg: Image):
    #     try:
    #         stamp = time.time()
    #         img = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")

    #         with self.image_lock:
    #             self.latest_bgr = img
    #             self.latest_stamp = stamp

    #             # Warn only for unusually slow camera updates to avoid log spam.
    #             if self.prev_image_stamp is not None:
    #                 frame_interval_ms = (stamp - self.prev_image_stamp) * 1000.0
    #                 if frame_interval_ms > 80.0:
    #                     self.get_logger().warning(
    #                         f"[CameraFrameDelay] frame_interval_ms={frame_interval_ms:.2f}"
    #                     )

    #             self.prev_image_stamp = stamp

    #     except Exception as e:
    #         self.get_logger().error(f"Image conversion failed: {e}")
    
    
    def on_image(self, msg: Image):
        try:
            stamp = time.time()
            img = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")

            with self.image_lock:
                self.latest_bgr = img
                self.latest_stamp = stamp
                self.prev_image_stamp = stamp
                self.image_count += 1

                # Keep a controlled recent-frame history.
                # Do not rely on the ROS subscriber queue for history.
                self.frame_buffer.append({
                    "image": img,
                    "stamp": stamp,
                })

        except Exception as e:
            self.get_logger().error(f"Image conversion failed: {e}")

    def publish_local_ack(self, obj_id, point_id, status):
        ros_ack = String()
        ros_ack.data = json.dumps({
            "obj_id": obj_id,
            "point_id": point_id,
            "status": status,
            "timestamp": time.time(),
        })
        self.ack_pub.publish(ros_ack)

    def publish_local_result(self, payload: dict):
        ros_result = String()
        ros_result.data = json.dumps({**payload, "timestamp": time.time()})
        self.result_pub.publish(ros_result)

    def get_best_image_for_request(
        self,
        request_recv_time: float,
        max_age_ms: float = RECENT_FRAME_MAX_AGE_MS,
        timeout_sec: float = REQUEST_IMAGE_TIMEOUT_SEC,
    ):
        """
        Select the best camera frame for a moving capture request.

        Priority:
          1) Choose the closest frame at/before the request time from a controlled
             internal buffer, if it is not too old.
          2) If no acceptable past/current frame exists, wait briefly for the next
             camera frame.
          3) If no usable frame exists, return timeout -> replay/no_fresh_image path.

        Why this design:
          - ROS QoS depth=1 prevents the subscriber from processing old queued frames.
          - The internal frame_buffer gives us a deliberate short history, so if the
            part just passed the capture point, we can still choose a recent frame
            from just before the request.
        """

        self.request_count += 1

        # ---------------------------------------------------------
        # 1) Try closest past/current frame from controlled buffer
        # ---------------------------------------------------------
        with self.image_lock:
            frames = list(self.frame_buffer)

        if frames:
            past_frames = [
                f for f in frames
                if f["stamp"] <= request_recv_time
            ]

            if past_frames:
                best = max(past_frames, key=lambda f: f["stamp"])
                age_ms = (request_recv_time - best["stamp"]) * 1000.0

                self.get_logger().info(
                    f"[RequestTiming] request_count={self.request_count} "
                    f"best_past_age_ms={age_ms:.2f} "
                    f"max_past_age_ms={MAX_PAST_FRAME_AGE_MS:.2f} "
                    f"buffer_size={len(frames)}"
                )

                if age_ms <= MAX_PAST_FRAME_AGE_MS:
                    return best["image"].copy(), best["stamp"], "buffer_past"

            else:
                self.get_logger().info(
                    f"[RequestTiming] request_count={self.request_count} "
                    f"no past frame in buffer | buffer_size={len(frames)}"
                )
        else:
            self.get_logger().warning(
                f"[RequestTiming] request_count={self.request_count} "
                f"no frames in buffer at request time"
            )

        # ---------------------------------------------------------
        # 2) If no good past frame, wait briefly for a newer frame
        # ---------------------------------------------------------
        initial_latest_stamp = frames[-1]["stamp"] if frames else None
        start = time.time()
        wait_limit = min(float(timeout_sec), float(MAX_FUTURE_FRAME_WAIT_SEC))

        while time.time() - start < wait_limit:
            with self.image_lock:
                latest_ref = self.latest_bgr
                latest_stamp = self.latest_stamp

            if latest_ref is not None and latest_stamp is not None:
                if initial_latest_stamp is None or latest_stamp > initial_latest_stamp:
                    wait_ms = (time.time() - start) * 1000.0
                    frame_delay_ms = (latest_stamp - request_recv_time) * 1000.0

                    self.get_logger().info(
                        f"[FramePick] next_frame selected "
                        f"wait_ms={wait_ms:.2f} "
                        f"frame_delay_from_request_ms={frame_delay_ms:.2f}"
                    )

                    return latest_ref.copy(), latest_stamp, "next_frame"

            time.sleep(0.005)

        # ---------------------------------------------------------
        # 3) No usable image
        # ---------------------------------------------------------
        with self.image_lock:
            final_stamp = self.latest_stamp
            final_buffer_size = len(self.frame_buffer)

        final_age_ms = None
        if final_stamp is not None:
            final_age_ms = (time.time() - final_stamp) * 1000.0

        self.get_logger().warning(
            f"[FramePickTimeout] no usable frame selected | "
            f"request_count={self.request_count} "
            f"buffer_size={final_buffer_size} "
            f"timeout_sec={wait_limit:.2f} "
            f"max_past_age_ms={MAX_PAST_FRAME_AGE_MS:.2f} "
            f"final_age_ms={final_age_ms if final_age_ms is not None else 'None'}"
        )

        return None, None, "timeout"

    def extract_send_image(self, full_bgr, obj_id, point_id):
        full_h, full_w = full_bgr.shape[:2]

        candidate_img = maybe_crop(full_bgr)
        fixed_crop_used = USE_ROI

        roi_found = False
        roi_bbox = []
        roi_status = "visibility_check_disabled" if not USE_BACKGROUND_ROI else "not_attempted"
        # If the visibility checker is disabled, allow full-frame sending.
        roi_valid = True if not USE_BACKGROUND_ROI else False
        roi_fallback_used = False
        roi_failure_reason = ""
        roi_aspect = ""
        roi_area = ""
        roi_center_error_x = ""
        roi_center_error_y = ""
        roi_blue_fraction = ""
        roi_border_touch = ""

        if USE_BACKGROUND_ROI and self.roi_detector is not None:
            debug_prefix = f"{obj_id}_p{point_id}_{int(time.time() * 1000)}"
            roi_img, bbox, roi_debug = self.roi_detector.detect(
                candidate_img, debug_prefix=debug_prefix
            )

            if roi_img is not None and bbox is not None:
                candidate_img = roi_img
                roi_bbox = list(bbox)
                roi_found = True
                roi_valid = bool(roi_debug.get("roi_valid", True))
                roi_fallback_used = bool(roi_debug.get("roi_fallback_used", False))
                roi_failure_reason = roi_debug.get("roi_failure_reason", "")
                roi_status = roi_debug.get("roi_status", "blue_tight_part_roi_ok")
                roi_aspect = roi_debug.get("aspect", "")
                roi_area = roi_debug.get("roi_area", roi_debug.get("merged_area", ""))
                roi_center_error_x = roi_debug.get("center_error_x", "")
                roi_center_error_y = roi_debug.get("center_error_y", "")
                roi_blue_fraction = roi_debug.get("final_blue_fraction", "")
                roi_border_touch = roi_debug.get("border_touch", "")
                self.get_logger().info(
                    f"ROI detected for {obj_id} point={point_id} | "
                    f"bbox={roi_bbox} aspect={roi_debug.get('aspect', '')} "
                    f"valid_contours={roi_debug.get('num_valid_contours', '')} "
                    f"blue_frac={roi_debug.get('final_blue_fraction', '')} roi_valid={roi_valid} fallback={roi_fallback_used}"
                )
            else:
                roi_status = roi_debug.get("error", "background_roi_failed")
                self.get_logger().warning(
                    f"ROI detection failed for {obj_id} point={point_id} | "
                    f"status={roi_status}"
                )
                if not ROI_FALLBACK_TO_FULL_FRAME:
                    return None, {
                        "full_width": full_w,
                        "full_height": full_h,
                        "roi_only": False,
                        "roi_used_for_visibility_check": bool(USE_BACKGROUND_ROI),
                        "roi_found": False,
                        "roi_status": roi_status,
                        "roi_bbox": [],
                        "roi_valid": False,
                        "roi_fallback_used": False,
                        "roi_failure_reason": roi_status,
                        "roi_aspect": "",
                        "roi_area": "",
                        "roi_center_error_x": "",
                        "roi_center_error_y": "",
                        "roi_blue_fraction": "",
                        "roi_border_touch": "",
                        "fixed_crop_used": fixed_crop_used,
                    }

        # Return the original full frame. Camera/render resolution is controlled
        # in Isaac; this relay should not hide a camera-control failure by
        # resizing after capture.
        # ROI metadata is used only as a
        # visibility/quality gate on the factory side. Actual ROI cropping for
        # YOLO is done on the edge side.
        return full_bgr, {
            "full_width": full_w,
            "full_height": full_h,
            "roi_only": False,
            "roi_used_for_visibility_check": bool(USE_BACKGROUND_ROI),
            "roi_found": roi_found,
            "roi_status": roi_status,
            "roi_bbox": roi_bbox,
            "roi_valid": roi_valid,
            "roi_fallback_used": roi_fallback_used,
            "roi_failure_reason": roi_failure_reason,
            "roi_aspect": roi_aspect,
            "roi_area": roi_area,
            "roi_center_error_x": roi_center_error_x,
            "roi_center_error_y": roi_center_error_y,
            "roi_blue_fraction": roi_blue_fraction,
            "roi_border_touch": roi_border_touch,
            "fixed_crop_used": fixed_crop_used,
        }

    def encode_image(self, bgr_img) -> Tuple[Optional[bytes], Optional[dict]]:
        if bgr_img is None:
            return None, None

        img = bgr_img
        h, w = img.shape[:2]
        c = img.shape[2] if len(img.shape) == 3 else 1
        raw_bytes = int(h * w * c)

        encode_start_ns = time.perf_counter_ns()

        if ENCODING == "raw":
            payload_bytes = img.tobytes()
        elif ENCODING == "png":
            ok, enc = cv2.imencode(".png", img)
            if not ok:
                return None, None
            payload_bytes = enc.tobytes()
        elif ENCODING == "jpeg":
            ok, enc = cv2.imencode(
                ".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
            )
            if not ok:
                return None, None
            payload_bytes = enc.tobytes()
        else:
            raise ValueError(f"Unsupported ENCODING={ENCODING}")

        encode_end_ns = time.perf_counter_ns()

        stats = {
            "width": w,
            "height": h,
            "channels": c,
            "dtype": "uint8",
            "encoding": ENCODING,
            "jpeg_quality": JPEG_QUALITY if ENCODING == "jpeg" else None,
            "raw_bytes": raw_bytes,
            "payload_bytes": len(payload_bytes),
            "encode_ms": (encode_end_ns - encode_start_ns) / 1e6,
            "encode_start_ns": encode_start_ns,
            "encode_end_ns": encode_end_ns,
        }
        return payload_bytes, stats

    def append_latency_row(self, row: dict):
        if not WRITE_LATENCY_CSV:
            return
        LATENCY_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_exists = LATENCY_CSV_PATH.exists()
        fieldnames = list(row.keys())
        if file_exists:
            try:
                with LATENCY_CSV_PATH.open(newline="") as existing:
                    existing_header = next(csv.reader(existing), [])
                if existing_header != fieldnames:
                    backup_path = LATENCY_CSV_PATH.with_name(
                        f"{LATENCY_CSV_PATH.stem}_schema_backup_{int(time.time())}{LATENCY_CSV_PATH.suffix}"
                    )
                    LATENCY_CSV_PATH.rename(backup_path)
                    self.get_logger().warning(
                        f"Latency CSV schema changed; moved old file to {backup_path}"
                    )
                    file_exists = False
            except Exception as exc:
                self.get_logger().warning(
                    f"Could not inspect existing latency CSV header; appending with current schema: {exc}"
                )
        with LATENCY_CSV_PATH.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

    def make_base_row(
        self,
        obj_id,
        point_id,
        part_name,
        expected_label,
        status,
        request_run_id="",
        capture_x="",
        capture_y="",
        capture_target_x="",
        capture_center_tol="",
        stable_ok="",
        fresh_frame_delay_ms="",
        frame_pick_mode="",
        sort_distance_x="",
        time_to_sort_sec="",
        available_sort_budget_ms="",
        late_for_sort_edge="",
        img_stats=None,
        predicted_decision="unknown",
        predicted_class="",
        predicted_confidence=0.0,
        correct="",
        bbox="[]",
        num_detections="",
        factory_send_ms="",
        factory_time_to_ack_ms="",
        factory_roundtrip_ms="",
        inference_ms="",
        edge_decode_ms="",
        edge_process_ms="",
        edge_total_ms="",
        edge_label="",
        edge_cpus="",
        edge_compute_capacity="",
        edge_resource_control="",
        edge_torch_threads="",
        combined_score="",
        roi_only="",
        roi_found="",
        roi_status="",
        roi_bbox="[]",
        roi_valid="",
        roi_fallback_used="",
        roi_failure_reason="",
        roi_aspect="",
        roi_area="",
        roi_center_error_x="",
        roi_center_error_y="",
        roi_blue_fraction="",
        roi_border_touch="",
        failure_type="",
        full_width="",
        full_height="",
        urllc_result_feedback_ms="",
        factory_send_start_wall_ns="",
        factory_send_end_wall_ns="",
        factory_ack_recv_wall_ns="",
        factory_result_recv_wall_ns="",
        factory_image_upload_ack_ms="",
        factory_ack_after_send_complete_ms="",
        factory_post_ack_to_result_ms="",
        edge_image_receive_ms="",
        edge_receive_complete_wall_ns="",
        edge_ack_feedback_ms="",
        edge_result_send_wall_ns="",
        edge_roi_found="",
        edge_roi_valid="",
        edge_roi_fallback_used="",
        edge_roi_status="",
        edge_roi_failure_reason="",
        edge_roi_bbox="[]",
        edge_roi_width="",
        edge_roi_height="",
        roi_rotated_before_inference="",
        roi_rotation_mode="",
        inference_image_width="",
        inference_image_height="",
    ):
        self.refresh_live_control_state_from_config()
        row = {
            "run_ts": time.time(),
            "factory_pid": os.getpid(),
            "request_run_id": request_run_id,
            "control_run_id": LIVE_CONTROL_STATE.get("run_id", ""),
            "control_speed": LIVE_CONTROL_STATE.get("speed", ""),
            "control_resolution": LIVE_CONTROL_STATE.get("resolution", ""),
            "control_prb": LIVE_CONTROL_STATE.get("prb", ""),
            "control_cpu": LIVE_CONTROL_STATE.get("cpu", ""),
            "control_updated_at": LIVE_CONTROL_STATE.get("updated_at", ""),
            "target_output_width": "",
            "target_output_height": "",
            "obj_id": obj_id,
            "point_id": point_id,
            "part_name": part_name,
            "expected_decision": expected_label,
            "predicted_decision": predicted_decision,
            "predicted_class": predicted_class,
            "predicted_confidence": predicted_confidence,
            "correct": correct,
            "status": status,
            "capture_x": capture_x,
            "capture_y": capture_y,
            "capture_target_x": capture_target_x,
            "capture_center_tol": capture_center_tol,
            "stable_ok": stable_ok,
            "fresh_frame_delay_ms": fresh_frame_delay_ms,
            "frame_pick_mode": frame_pick_mode,
            "sort_distance_x": sort_distance_x,
            "time_to_sort_sec": time_to_sort_sec,
            "available_sort_budget_ms": available_sort_budget_ms,
            "late_for_sort_edge": late_for_sort_edge,
            "roi_only": roi_only,
            "roi_found": roi_found,
            "roi_status": roi_status,
            "roi_bbox": roi_bbox,
            "roi_valid": roi_valid,
            "roi_fallback_used": roi_fallback_used,
            "roi_failure_reason": roi_failure_reason,
            "roi_aspect": roi_aspect,
            "roi_area": roi_area,
            "roi_center_error_x": roi_center_error_x,
            "roi_center_error_y": roi_center_error_y,
            "roi_blue_fraction": roi_blue_fraction,
            "roi_border_touch": roi_border_touch,
            "failure_type": failure_type,
            "full_width": full_width,
            "full_height": full_height,
        }

        if img_stats is not None:
            live_cpu = LIVE_CONTROL_STATE.get("cpu", "")
            row.update({
                "width": img_stats["width"],
                "height": img_stats["height"],
                "channels": img_stats["channels"],
                "resolution": f"{img_stats['width']}x{img_stats['height']}",
                "sent_resolution": f"{img_stats['width']}x{img_stats['height']}",
                "encoding": img_stats["encoding"],
                "jpeg_quality": img_stats["jpeg_quality"] if img_stats["jpeg_quality"] is not None else "",
                "raw_bytes": img_stats["raw_bytes"],
                "payload_bytes": img_stats["payload_bytes"],
                "bbox": bbox,
                "num_detections": num_detections,
                "factory_encode_ms": round(img_stats["encode_ms"], 3),
                "factory_send_ms": factory_send_ms,
                "factory_time_to_ack_ms": factory_time_to_ack_ms,
                "factory_image_upload_ack_ms": factory_image_upload_ack_ms,
                "factory_ack_after_send_complete_ms": factory_ack_after_send_complete_ms,
                "factory_roundtrip_ms": factory_roundtrip_ms,
                "factory_post_ack_to_result_ms": factory_post_ack_to_result_ms,
                "inference_ms": inference_ms,
                "edge_decode_ms": edge_decode_ms,
                "edge_process_ms": edge_process_ms,
                "edge_total_ms": edge_total_ms,
                "edge_label": edge_label,
                "edge_reported_cpus": edge_cpus,
                "edge_reported_compute_capacity": edge_compute_capacity,
                "edge_live_cpus": live_cpu,
                "edge_cpus": live_cpu or edge_cpus,
                "edge_compute_capacity": edge_compute_capacity,
                "edge_resource_control": edge_resource_control,
                "edge_torch_threads": edge_torch_threads,
                "edge_image_receive_ms": edge_image_receive_ms,
                "edge_ack_feedback_ms": edge_ack_feedback_ms,
                "edge_receive_complete_wall_ns": edge_receive_complete_wall_ns,
                "edge_result_send_wall_ns": edge_result_send_wall_ns,
                "edge_roi_found": edge_roi_found,
                "edge_roi_valid": edge_roi_valid,
                "edge_roi_fallback_used": edge_roi_fallback_used,
                "edge_roi_status": edge_roi_status,
                "edge_roi_failure_reason": edge_roi_failure_reason,
                "edge_roi_bbox": edge_roi_bbox,
                "edge_roi_width": edge_roi_width,
                "edge_roi_height": edge_roi_height,
                "roi_rotated_before_inference": roi_rotated_before_inference,
                "roi_rotation_mode": roi_rotation_mode,
                "inference_image_width": inference_image_width,
                "inference_image_height": inference_image_height,
                "factory_send_start_wall_ns": factory_send_start_wall_ns,
                "factory_send_end_wall_ns": factory_send_end_wall_ns,
                "factory_ack_recv_wall_ns": factory_ack_recv_wall_ns,
                "factory_result_recv_wall_ns": factory_result_recv_wall_ns,
                "combined_score": combined_score,
                "urllc_result_feedback_ms": urllc_result_feedback_ms,
            })
        else:
            live_cpu = LIVE_CONTROL_STATE.get("cpu", "")
            row.update({
                "width": "",
                "height": "",
                "channels": "",
                "resolution": "",
                "sent_resolution": "",
                "encoding": "",
                "jpeg_quality": "",
                "raw_bytes": "",
                "payload_bytes": "",
                "bbox": bbox,
                "num_detections": num_detections,
                "factory_encode_ms": "",
                "factory_send_ms": factory_send_ms,
                "factory_time_to_ack_ms": factory_time_to_ack_ms,
                "factory_image_upload_ack_ms": factory_image_upload_ack_ms,
                "factory_ack_after_send_complete_ms": factory_ack_after_send_complete_ms,
                "factory_roundtrip_ms": factory_roundtrip_ms,
                "factory_post_ack_to_result_ms": factory_post_ack_to_result_ms,
                "inference_ms": inference_ms,
                "edge_decode_ms": edge_decode_ms,
                "edge_process_ms": edge_process_ms,
                "edge_total_ms": edge_total_ms,
                "edge_label": edge_label,
                "edge_reported_cpus": edge_cpus,
                "edge_reported_compute_capacity": edge_compute_capacity,
                "edge_live_cpus": live_cpu,
                "edge_cpus": live_cpu or edge_cpus,
                "edge_compute_capacity": edge_compute_capacity,
                "edge_resource_control": edge_resource_control,
                "edge_torch_threads": edge_torch_threads,
                "edge_image_receive_ms": edge_image_receive_ms,
                "edge_ack_feedback_ms": edge_ack_feedback_ms,
                "edge_receive_complete_wall_ns": edge_receive_complete_wall_ns,
                "edge_result_send_wall_ns": edge_result_send_wall_ns,
                "edge_roi_found": edge_roi_found,
                "edge_roi_valid": edge_roi_valid,
                "edge_roi_fallback_used": edge_roi_fallback_used,
                "edge_roi_status": edge_roi_status,
                "edge_roi_failure_reason": edge_roi_failure_reason,
                "edge_roi_bbox": edge_roi_bbox,
                "edge_roi_width": edge_roi_width,
                "edge_roi_height": edge_roi_height,
                "roi_rotated_before_inference": roi_rotated_before_inference,
                "roi_rotation_mode": roi_rotation_mode,
                "inference_image_width": inference_image_width,
                "inference_image_height": inference_image_height,
                "factory_send_start_wall_ns": factory_send_start_wall_ns,
                "factory_send_end_wall_ns": factory_send_end_wall_ns,
                "factory_ack_recv_wall_ns": factory_ack_recv_wall_ns,
                "factory_result_recv_wall_ns": factory_result_recv_wall_ns,
                "combined_score": combined_score,
                "urllc_result_feedback_ms": urllc_result_feedback_ms,
            })

        return row

    def on_request(self, msg: String):
        try:
            req = json.loads(msg.data)
        except Exception as e:
            self.get_logger().error(f"Failed to parse request JSON: {e}")
            return

        if req.get("mode") != "inspect":
            return

        obj_id = req.get("obj_id", "unknown_obj")
        point_id = int(req.get("point_id", -1))
        part_name = req.get("part_name", "unknown_part")
        expected_label = req.get("label", "")
        request_run_id = req.get("run_id", "")

        if not self.inflight_sem.acquire(blocking=False):
            status = "dropped_factory_inflight_limit"
            self.get_logger().warning(
                f"Factory in-flight limit reached ({MAX_FACTORY_IN_FLIGHT}); "
                f"dropping obj={obj_id} point={point_id}"
            )
            self.publish_local_ack(obj_id, point_id, status)
            self.publish_local_result({
                "obj_id": obj_id,
                "point_id": point_id,
                "decision": "unknown",
                "combined_score": 0.0,
                "part_name": part_name,
                "status": status,
            })
            self.append_latency_row(
                self.make_base_row(
                    obj_id=obj_id,
                    point_id=point_id,
                    part_name=part_name,
                    expected_label=expected_label,
                    status=status,
                    request_run_id=request_run_id,
                    predicted_decision="unknown",
                    predicted_class="",
                    predicted_confidence=0.0,
                    correct="",
                    bbox="[]",
                    num_detections=0,
                )
            )
            return

        threading.Thread(
            target=self._run_request_with_release,
            args=(req,),
            daemon=True,
        ).start()

    def _run_request_with_release(self, req: dict):
        try:
            self.process_request(req)
        finally:
            self.inflight_sem.release()

    def process_request(self, req: dict):
        obj_id = req.get("obj_id", "unknown_obj")
        point_id = int(req.get("point_id", -1))
        part_name = req.get("part_name", "unknown_part")
        expected_label = req.get("label", "")
        request_run_id = req.get("run_id", "")

        capture_x = req.get("capture_x", "")
        capture_y = req.get("capture_y", "")
        capture_target_x = req.get("capture_target_x", "")
        capture_center_tol = req.get("capture_center_tol", "")
        stable_ok = req.get("stable_ok", "")

        sort_distance_x = req.get("sort_distance_x", "")
        time_to_sort_sec = req.get("time_to_sort_sec", "")

        available_sort_budget_ms = ""
        if time_to_sort_sec != "":
            try:
                available_sort_budget_ms = float(time_to_sort_sec) * 1000.0
            except Exception:
                available_sort_budget_ms = ""

        request_recv_time = time.time()

        fresh_img, fresh_stamp, frame_pick_mode = self.get_best_image_for_request(
            request_recv_time=request_recv_time,
            max_age_ms=RECENT_FRAME_MAX_AGE_MS,
            timeout_sec=REQUEST_IMAGE_TIMEOUT_SEC,
        )

        # if fresh_img is None:
        #     self.get_logger().warning(
        #         f"No usable image available for request | mode={frame_pick_mode}"
        #     )
        #     self.publish_local_ack(obj_id, point_id, "no_fresh_image")
        #     self.publish_local_result({
        #         "obj_id": obj_id,
        #         "point_id": point_id,
        #         "decision": "unknown",
        #         "combined_score": 0.0,
        #         "part_name": part_name,
        #         "status": "no_fresh_image",
        #     })

        #     self.append_latency_row(
        #         self.make_base_row(
        #             obj_id=obj_id,
        #             point_id=point_id,
        #             part_name=part_name,
        #             expected_label=expected_label,
        #             status="no_fresh_image",
        #             capture_x=capture_x,
        #             capture_y=capture_y,
        #             capture_target_x=capture_target_x,
        #             capture_center_tol=capture_center_tol,
        #             stable_ok=stable_ok,
        #             fresh_frame_delay_ms="",
        #             frame_pick_mode=frame_pick_mode,
        #             sort_distance_x=sort_distance_x,
        #             time_to_sort_sec=time_to_sort_sec,
        #             available_sort_budget_ms=available_sort_budget_ms,
        #             late_for_sort_edge="",
        #             predicted_decision="unknown",
        #             predicted_class="",
        #             predicted_confidence=0.0,
        #             correct="",
        #             bbox="[]",
        #             num_detections=0,
        #         )
        #     )
        #     return

        # checked_img, roi_meta = self.extract_send_image(
        #     fresh_img, obj_id=obj_id, point_id=point_id
        # )
        
        
        if fresh_img is None:
            replay_status = "replay_required_no_fresh_image"
            replay_failure_type = "capture_no_fresh_image"

            self.get_logger().warning(
                f"No usable image available for request | mode={frame_pick_mode}; "
                f"requesting replay for obj={obj_id} point={point_id}"
            )

            self.publish_local_ack(obj_id, point_id, replay_status)

            self.publish_local_result({
                "obj_id": obj_id,
                "point_id": point_id,
                "decision": "unknown",
                "combined_score": 0.0,
                "part_name": part_name,
                "status": replay_status,
                "failure_type": replay_failure_type,
                "frame_pick_mode": frame_pick_mode,
            })

            self.append_latency_row(
                self.make_base_row(
                    obj_id=obj_id,
                    point_id=point_id,
                    part_name=part_name,
                    expected_label=expected_label,
                    status=replay_status,
                    request_run_id=request_run_id,
                    capture_x=capture_x,
                    capture_y=capture_y,
                    capture_target_x=capture_target_x,
                    capture_center_tol=capture_center_tol,
                    stable_ok=stable_ok,
                    fresh_frame_delay_ms="",
                    frame_pick_mode=frame_pick_mode,
                    sort_distance_x=sort_distance_x,
                    time_to_sort_sec=time_to_sort_sec,
                    available_sort_budget_ms=available_sort_budget_ms,
                    late_for_sort_edge="",
                    predicted_decision="unknown",
                    predicted_class="",
                    predicted_confidence=0.0,
                    correct="",
                    bbox="[]",
                    num_detections=0,
                    failure_type=replay_failure_type,
                )
            )
            return

        checked_img, roi_meta = self.extract_send_image(
            fresh_img, obj_id=obj_id, point_id=point_id
        )

        # Factory-side ROI detector is now only a visibility gate.
        # If the part is partial / not fully inside the camera frame, do not
        # transmit it. Ask the controller to replay the same source object.
        if checked_img is None or not bool(roi_meta.get("roi_valid", False)):
            replay_status = "replay_required_partial_visibility"
            replay_failure_type = "partial_or_not_fully_visible"
            self.get_logger().warning(
                f"Visibility check failed for obj={obj_id} point={point_id}; "
                f"requesting replay | roi_status={roi_meta.get('roi_status', '')} "
                f"reason={roi_meta.get('roi_failure_reason', '')}"
            )
            self.publish_local_ack(obj_id, point_id, replay_status)
            self.publish_local_result({
                "obj_id": obj_id,
                "point_id": point_id,
                "decision": "unknown",
                "combined_score": 0.0,
                "part_name": part_name,
                "status": replay_status,
                "failure_type": replay_failure_type,
                "roi_status": roi_meta.get("roi_status", ""),
                "roi_failure_reason": roi_meta.get("roi_failure_reason", ""),
                "roi_bbox": roi_meta.get("roi_bbox", []),
                "roi_valid": bool(roi_meta.get("roi_valid", False)),
                "roi_fallback_used": bool(roi_meta.get("roi_fallback_used", False)),
            })

            fresh_frame_delay_ms = (fresh_stamp - request_recv_time) * 1000.0 if fresh_stamp is not None else ""
            self.append_latency_row(
                self.make_base_row(
                    obj_id=obj_id,
                    point_id=point_id,
                    part_name=part_name,
                    expected_label=expected_label,
                    status=replay_status,
                    request_run_id=request_run_id,
                    capture_x=capture_x,
                    capture_y=capture_y,
                    capture_target_x=capture_target_x,
                    capture_center_tol=capture_center_tol,
                    stable_ok=stable_ok,
                    fresh_frame_delay_ms=round(fresh_frame_delay_ms, 3) if fresh_frame_delay_ms != "" else "",
                    frame_pick_mode=frame_pick_mode,
                    sort_distance_x=sort_distance_x,
                    time_to_sort_sec=time_to_sort_sec,
                    available_sort_budget_ms=available_sort_budget_ms,
                    late_for_sort_edge="",
                    predicted_decision="unknown",
                    predicted_class="",
                    predicted_confidence=0.0,
                    correct="",
                    bbox="[]",
                    num_detections=0,
                    roi_only=int(bool(roi_meta.get("roi_only", False))),
                    roi_found=int(bool(roi_meta.get("roi_found", False))),
                    roi_status=roi_meta.get("roi_status", ""),
                    roi_bbox=json.dumps(roi_meta.get("roi_bbox", [])),
                    roi_valid=int(bool(roi_meta.get("roi_valid", False))),
                    roi_fallback_used=int(bool(roi_meta.get("roi_fallback_used", False))),
                    roi_failure_reason=roi_meta.get("roi_failure_reason", ""),
                    roi_aspect=roi_meta.get("roi_aspect", ""),
                    roi_area=roi_meta.get("roi_area", ""),
                    roi_center_error_x=roi_meta.get("roi_center_error_x", ""),
                    roi_center_error_y=roi_meta.get("roi_center_error_y", ""),
                    roi_blue_fraction=roi_meta.get("roi_blue_fraction", ""),
                    roi_border_touch=roi_meta.get("roi_border_touch", ""),
                    failure_type=replay_failure_type,
                    full_width=roi_meta.get("full_width", ""),
                    full_height=roi_meta.get("full_height", ""),
                )
            )
            return

        # Send the visibility-checked frame exactly as received from Isaac.
        # Resolution changes must happen at Isaac's render product.
        image_bytes, img_stats = self.encode_image(checked_img)
        if image_bytes is None:
            self.get_logger().warning("Failed to encode selected image")
            self.publish_local_ack(obj_id, point_id, "encode_failed")
            self.publish_local_result({
                "obj_id": obj_id,
                "point_id": point_id,
                "decision": "unknown",
                "combined_score": 0.0,
                "part_name": part_name,
                "status": "encode_failed",
            })

            fresh_frame_delay_ms = (fresh_stamp - request_recv_time) * 1000.0 if fresh_stamp is not None else ""

            self.append_latency_row(
                self.make_base_row(
                    obj_id=obj_id,
                    point_id=point_id,
                    part_name=part_name,
                    expected_label=expected_label,
                    status="encode_failed",
                    request_run_id=request_run_id,
                    capture_x=capture_x,
                    capture_y=capture_y,
                    capture_target_x=capture_target_x,
                    capture_center_tol=capture_center_tol,
                    stable_ok=stable_ok,
                    fresh_frame_delay_ms=round(fresh_frame_delay_ms, 3) if fresh_frame_delay_ms != "" else "",
                    frame_pick_mode=frame_pick_mode,
                    sort_distance_x=sort_distance_x,
                    time_to_sort_sec=time_to_sort_sec,
                    available_sort_budget_ms=available_sort_budget_ms,
                    late_for_sort_edge="",
                    img_stats=img_stats,
                    predicted_decision="unknown",
                    predicted_class="",
                    predicted_confidence=0.0,
                    correct="",
                    bbox="[]",
                    num_detections=0,
                    roi_only=int(roi_meta["roi_only"]),
                    roi_found=int(roi_meta["roi_found"]),
                    roi_status=roi_meta["roi_status"],
                    roi_bbox=json.dumps(roi_meta["roi_bbox"]),
                    full_width=roi_meta["full_width"],
                    full_height=roi_meta["full_height"],
                )
            )
            return

        fresh_frame_delay_ms = (fresh_stamp - request_recv_time) * 1000.0 if fresh_stamp is not None else ""

        sock = None
        ack_recv_ns = None
        result_recv_ns = None
        send_start_ns = None
        send_end_ns = None
        send_start_wall_ns = ""
        send_end_wall_ns = ""
        ack_recv_wall_ns = ""
        result_recv_wall_ns = ""

        try:
            header_payload = dict(req)
            header_payload["factory_pid"] = os.getpid()
            header_payload["control_run_id"] = LIVE_CONTROL_STATE.get("run_id", "")
            header_payload["control_speed"] = LIVE_CONTROL_STATE.get("speed", "")
            header_payload["control_resolution"] = LIVE_CONTROL_STATE.get("resolution", "")
            header_payload["control_prb"] = LIVE_CONTROL_STATE.get("prb", "")
            header_payload["control_cpu"] = LIVE_CONTROL_STATE.get("cpu", "")
            header_payload["target_output_width"] = ""
            header_payload["target_output_height"] = ""
            header_payload["image_encoding"] = img_stats["encoding"]
            header_payload["image_width"] = img_stats["width"]
            header_payload["image_height"] = img_stats["height"]
            header_payload["image_channels"] = img_stats["channels"]
            header_payload["image_dtype"] = img_stats["dtype"]
            header_payload["raw_bytes"] = img_stats["raw_bytes"]
            header_payload["payload_bytes"] = img_stats["payload_bytes"]
            header_payload["factory_encode_ms"] = img_stats["encode_ms"]
            header_payload["factory_encode_start_ns"] = img_stats["encode_start_ns"]
            header_payload["factory_encode_end_ns"] = img_stats["encode_end_ns"]
            header_payload["fresh_frame_delay_ms"] = fresh_frame_delay_ms
            header_payload["frame_pick_mode"] = frame_pick_mode

            header_payload["roi_only"] = roi_meta["roi_only"]
            header_payload["roi_used_for_visibility_check"] = roi_meta.get("roi_used_for_visibility_check", False)
            header_payload["roi_found"] = roi_meta["roi_found"]
            header_payload["roi_valid"] = roi_meta.get("roi_valid", "")
            header_payload["roi_fallback_used"] = roi_meta.get("roi_fallback_used", "")
            header_payload["roi_failure_reason"] = roi_meta.get("roi_failure_reason", "")
            header_payload["roi_status"] = roi_meta["roi_status"]
            header_payload["roi_bbox"] = roi_meta["roi_bbox"]
            header_payload["full_width"] = roi_meta["full_width"]
            header_payload["full_height"] = roi_meta["full_height"]

            if img_stats["jpeg_quality"] is not None:
                header_payload["jpeg_quality"] = img_stats["jpeg_quality"]

            payload = {
                "type": "request",
                "payload": header_payload,
                "image_len": len(image_bytes),
            }

            log_msg = (
                f"Connecting to UE forwarder at {UE_FORWARDER_IP}:{UE_FORWARDER_PORT} | "
                f"send={img_stats['width']}x{img_stats['height']}x{img_stats['channels']} "
                f"control_resolution={LIVE_CONTROL_STATE.get('resolution', '')} "
                f"pid={os.getpid()} "
                f"encoding={img_stats['encoding']} raw={img_stats['raw_bytes']}B "
                f"payload={img_stats['payload_bytes']}B "
                f"frame_pick_mode={frame_pick_mode} "
                f"factory_visibility_roi_found={roi_meta['roi_found']} factory_visibility_roi_status={roi_meta['roi_status']}"
            )
            if fresh_frame_delay_ms != "":
                log_msg += f" fresh_frame_delay_ms={fresh_frame_delay_ms:.3f}"
            self.get_logger().info(log_msg)

            sock = socket.create_connection(
                (UE_FORWARDER_IP, UE_FORWARDER_PORT),
                timeout=SOCKET_TIMEOUT_SEC
            )
            sock.settimeout(SOCKET_TIMEOUT_SEC)
            reader = JsonLineReader(sock)

            local_ip, local_port = sock.getsockname()
            self.get_logger().info(
                f"Connected to UE forwarder at {UE_FORWARDER_IP}:{UE_FORWARDER_PORT} "
                f"from local {local_ip}:{local_port}"
            )

            send_start_ns = time.perf_counter_ns()
            send_start_wall_ns = time.time_ns()
            send_json_line(sock, payload)
            sock.sendall(image_bytes)
            send_end_ns = time.perf_counter_ns()
            send_end_wall_ns = time.time_ns()

            self.get_logger().info(
                f"Sent header+image to UE forwarder: obj={obj_id} point={point_id} "
                f"part={part_name} roi_bbox={roi_meta['roi_bbox']}"
            )

            #ack = recv_json_line(sock)
            ack = reader.recv_json()
            ack_recv_ns = time.perf_counter_ns()
            ack_recv_wall_ns = time.time_ns()
            if ack is None:
                self.get_logger().error("No ACK received from UE forwarder")
                self.append_latency_row(
                    self.make_base_row(
                        obj_id=obj_id,
                        point_id=point_id,
                        part_name=part_name,
                        expected_label=expected_label,
                        status="no_ack_from_ue_forwarder",
                        request_run_id=request_run_id,
                        capture_x=capture_x,
                        capture_y=capture_y,
                        capture_target_x=capture_target_x,
                        capture_center_tol=capture_center_tol,
                        stable_ok=stable_ok,
                        fresh_frame_delay_ms=round(fresh_frame_delay_ms, 3) if fresh_frame_delay_ms != "" else "",
                        frame_pick_mode=frame_pick_mode,
                        sort_distance_x=sort_distance_x,
                        time_to_sort_sec=time_to_sort_sec,
                        available_sort_budget_ms=available_sort_budget_ms,
                        late_for_sort_edge="",
                        img_stats=img_stats,
                        predicted_decision="unknown",
                        predicted_class="",
                        predicted_confidence=0.0,
                        correct="",
                        bbox="[]",
                        num_detections=0,
                        factory_send_ms=round((send_end_ns - send_start_ns) / 1e6, 3),
                        roi_only=int(roi_meta["roi_only"]),
                        roi_found=int(roi_meta["roi_found"]),
                        roi_status=roi_meta["roi_status"],
                        roi_bbox=json.dumps(roi_meta["roi_bbox"]),
                        full_width=roi_meta["full_width"],
                        full_height=roi_meta["full_height"],
                    )
                )
                return

            if ack.get("type") == "ack":
                ack_payload = ack.get("payload", {})
                self.publish_local_ack(
                    obj_id=ack_payload.get("obj_id", obj_id),
                    point_id=ack_payload.get("point_id", point_id),
                    status=ack_payload.get("status", "received"),
                )
                self.get_logger().info(f"Published ACK locally: {ack_payload}")
            else:
                ack_payload = {}
                self.get_logger().warning(f"Expected ACK, got: {ack}")

            #result = recv_json_line(sock)
            # result = reader.recv_json()
            # result_recv_ns = time.perf_counter_ns()
            result = reader.recv_json()
            result_recv_ns = time.perf_counter_ns()
            result_recv_wall_ns = time.time_ns()
            if result is None:
                self.get_logger().error("No result received from UE forwarder")
                self.append_latency_row(
                    self.make_base_row(
                        obj_id=obj_id,
                        point_id=point_id,
                        part_name=part_name,
                        expected_label=expected_label,
                        status="no_result_from_ue_forwarder",
                        request_run_id=request_run_id,
                        capture_x=capture_x,
                        capture_y=capture_y,
                        capture_target_x=capture_target_x,
                        capture_center_tol=capture_center_tol,
                        stable_ok=stable_ok,
                        fresh_frame_delay_ms=round(fresh_frame_delay_ms, 3) if fresh_frame_delay_ms != "" else "",
                        frame_pick_mode=frame_pick_mode,
                        sort_distance_x=sort_distance_x,
                        time_to_sort_sec=time_to_sort_sec,
                        available_sort_budget_ms=available_sort_budget_ms,
                        late_for_sort_edge="",
                        img_stats=img_stats,
                        predicted_decision="unknown",
                        predicted_class="",
                        predicted_confidence=0.0,
                        correct="",
                        bbox="[]",
                        num_detections=0,
                        factory_send_ms=round((send_end_ns - send_start_ns) / 1e6, 3),
                        factory_time_to_ack_ms=round((ack_recv_ns - send_start_ns) / 1e6, 3) if ack_recv_ns else "",
                        roi_only=int(roi_meta["roi_only"]),
                        roi_found=int(roi_meta["roi_found"]),
                        roi_status=roi_meta["roi_status"],
                        roi_bbox=json.dumps(roi_meta["roi_bbox"]),
                        full_width=roi_meta["full_width"],
                        full_height=roi_meta["full_height"],
                    )
                )
                return

            if result.get("type") != "result":
                self.get_logger().warning(f"Expected result, got: {result}")
                self.append_latency_row(
                    self.make_base_row(
                        obj_id=obj_id,
                        point_id=point_id,
                        part_name=part_name,
                        expected_label=expected_label,
                        status="unexpected_result_type",
                        request_run_id=request_run_id,
                        capture_x=capture_x,
                        capture_y=capture_y,
                        capture_target_x=capture_target_x,
                        capture_center_tol=capture_center_tol,
                        stable_ok=stable_ok,
                        fresh_frame_delay_ms=round(fresh_frame_delay_ms, 3) if fresh_frame_delay_ms != "" else "",
                        frame_pick_mode=frame_pick_mode,
                        sort_distance_x=sort_distance_x,
                        time_to_sort_sec=time_to_sort_sec,
                        available_sort_budget_ms=available_sort_budget_ms,
                        late_for_sort_edge="",
                        img_stats=img_stats,
                        predicted_decision="unknown",
                        predicted_class="",
                        predicted_confidence=0.0,
                        correct="",
                        bbox="[]",
                        num_detections=0,
                        factory_send_ms=round((send_end_ns - send_start_ns) / 1e6, 3),
                        factory_time_to_ack_ms=round((ack_recv_ns - send_start_ns) / 1e6, 3) if ack_recv_ns else "",
                        factory_roundtrip_ms=round((result_recv_ns - send_start_ns) / 1e6, 3) if result_recv_ns else "",
                        roi_only=int(roi_meta["roi_only"]),
                        roi_found=int(roi_meta["roi_found"]),
                        roi_status=roi_meta["roi_status"],
                        roi_bbox=json.dumps(roi_meta["roi_bbox"]),
                        full_width=roi_meta["full_width"],
                        full_height=roi_meta["full_height"],
                    )
                )
                return

            result_payload = result["payload"]
            
            urllc_result_feedback_ms = ""
            factory_time_to_ack_ms = round((ack_recv_ns - send_start_ns) / 1e6, 3) if ack_recv_ns else ""
            factory_ack_after_send_complete_ms = round((ack_recv_ns - send_end_ns) / 1e6, 3) if ack_recv_ns and send_end_ns else ""
            factory_post_ack_to_result_ms = round((result_recv_ns - ack_recv_ns) / 1e6, 3) if result_recv_ns and ack_recv_ns else ""
            edge_image_receive_ms = ack_payload.get(
                "edge_image_receive_ms",
                ack_payload.get("edge_ack_ms", ""),
            )
            edge_receive_complete_wall_ns = ack_payload.get("edge_receive_complete_wall_ns", "")
            edge_ack_feedback_ms = ""
            if edge_receive_complete_wall_ns != "":
                try:
                    edge_ack_feedback_ms = round(
                        (ack_recv_wall_ns - int(edge_receive_complete_wall_ns)) / 1e6,
                        3,
                    )
                except Exception:
                    edge_ack_feedback_ms = ""

            edge_result_send_wall_ns = result_payload.get("edge_result_send_wall_ns", "")
            if edge_result_send_wall_ns != "":
                try:
                    urllc_result_feedback_ms = round(
                        (result_recv_wall_ns - int(edge_result_send_wall_ns)) / 1e6,
                        3
                    )
                except Exception:
                    urllc_result_feedback_ms = ""

            factory_roundtrip_ms = round((result_recv_ns - send_start_ns) / 1e6, 3) if result_recv_ns else ""
            late_for_sort_edge = False

            if available_sort_budget_ms != "" and factory_roundtrip_ms != "":
                try:
                    late_for_sort_edge = float(factory_roundtrip_ms) > float(available_sort_budget_ms)
                except Exception:
                    late_for_sort_edge = False

            predicted_class = result_payload.get("predicted_class", "")
            predicted_confidence = result_payload.get("predicted_confidence", "")
            predicted_binary = result_payload.get("decision", "unknown")
            correct = int(expected_label == predicted_class) if expected_label else ""

            if late_for_sort_edge:
                result_payload["status"] = f"{result_payload.get('status', 'ok')}|late_for_sort_edge"

            self.publish_local_result(result_payload)
            self.get_logger().info(f"Published result locally: {result_payload}")

            if late_for_sort_edge:
                failure_type = "late_for_sort"
            elif roi_meta.get("roi_found") and not roi_meta.get("roi_valid", True):
                failure_type = "roi_fallback_low_quality"
            elif expected_label and predicted_class != expected_label:
                failure_type = "model_miss"
            else:
                failure_type = "ok"

            row = self.make_base_row(
                obj_id=obj_id,
                point_id=point_id,
                part_name=part_name,
                expected_label=expected_label,
                status=result_payload.get("status", ""),
                request_run_id=request_run_id,
                capture_x=capture_x,
                capture_y=capture_y,
                capture_target_x=capture_target_x,
                capture_center_tol=capture_center_tol,
                stable_ok=stable_ok,
                fresh_frame_delay_ms=round(fresh_frame_delay_ms, 3) if fresh_frame_delay_ms != "" else "",
                frame_pick_mode=frame_pick_mode,
                sort_distance_x=sort_distance_x,
                time_to_sort_sec=time_to_sort_sec,
                available_sort_budget_ms=available_sort_budget_ms,
                late_for_sort_edge=int(late_for_sort_edge),
                img_stats=img_stats,
                predicted_decision=predicted_binary,
                predicted_class=predicted_class,
                predicted_confidence=predicted_confidence,
                correct=correct,
                bbox=json.dumps(result_payload.get("bbox", [])),
                num_detections=result_payload.get("num_detections", ""),
                factory_send_ms=round((send_end_ns - send_start_ns) / 1e6, 3),
                factory_time_to_ack_ms=factory_time_to_ack_ms,
                factory_image_upload_ack_ms=factory_time_to_ack_ms,
                factory_ack_after_send_complete_ms=factory_ack_after_send_complete_ms,
                factory_roundtrip_ms=factory_roundtrip_ms,
                factory_post_ack_to_result_ms=factory_post_ack_to_result_ms,
                inference_ms=result_payload.get("inference_ms", ""),
                edge_decode_ms=result_payload.get("edge_decode_ms", ""),
                edge_process_ms=result_payload.get("edge_process_ms", ""),
                edge_total_ms=result_payload.get("edge_total_ms", ""),
                edge_label=result_payload.get("edge_label", ""),
                edge_cpus=result_payload.get("edge_cpus", ""),
                edge_compute_capacity=result_payload.get("edge_compute_capacity", ""),
                edge_resource_control=result_payload.get("edge_resource_control", ""),
                edge_torch_threads=result_payload.get("edge_torch_threads", ""),
                edge_image_receive_ms=edge_image_receive_ms,
                edge_receive_complete_wall_ns=edge_receive_complete_wall_ns,
                edge_ack_feedback_ms=edge_ack_feedback_ms,
                edge_result_send_wall_ns=edge_result_send_wall_ns,
                edge_roi_found=result_payload.get("edge_roi_found", ""),
                edge_roi_valid=result_payload.get("edge_roi_valid", ""),
                edge_roi_fallback_used=result_payload.get("edge_roi_fallback_used", ""),
                edge_roi_status=result_payload.get("edge_roi_status", ""),
                edge_roi_failure_reason=result_payload.get("edge_roi_failure_reason", ""),
                edge_roi_bbox=json.dumps(result_payload.get("edge_roi_bbox", [])),
                edge_roi_width=result_payload.get("edge_roi_width", ""),
                edge_roi_height=result_payload.get("edge_roi_height", ""),
                roi_rotated_before_inference=result_payload.get("roi_rotated_before_inference", ""),
                roi_rotation_mode=result_payload.get("roi_rotation_mode", ""),
                inference_image_width=result_payload.get("inference_image_width", ""),
                inference_image_height=result_payload.get("inference_image_height", ""),
                combined_score=result_payload.get("combined_score", ""),
                roi_only=int(roi_meta["roi_only"]),
                roi_found=int(roi_meta["roi_found"]),
                roi_status=roi_meta["roi_status"],
                roi_bbox=json.dumps(roi_meta["roi_bbox"]),
                roi_valid=int(bool(roi_meta.get("roi_valid", False))),
                roi_fallback_used=int(bool(roi_meta.get("roi_fallback_used", False))),
                roi_failure_reason=roi_meta.get("roi_failure_reason", ""),
                roi_aspect=roi_meta.get("roi_aspect", ""),
                roi_area=roi_meta.get("roi_area", ""),
                roi_center_error_x=roi_meta.get("roi_center_error_x", ""),
                roi_center_error_y=roi_meta.get("roi_center_error_y", ""),
                roi_blue_fraction=roi_meta.get("roi_blue_fraction", ""),
                roi_border_touch=roi_meta.get("roi_border_touch", ""),
                failure_type=failure_type,
                full_width=roi_meta["full_width"],
                full_height=roi_meta["full_height"],
                urllc_result_feedback_ms=urllc_result_feedback_ms,
                factory_send_start_wall_ns=send_start_wall_ns,
                factory_send_end_wall_ns=send_end_wall_ns,
                factory_ack_recv_wall_ns=ack_recv_wall_ns,
                factory_result_recv_wall_ns=result_recv_wall_ns,
                
            )

            self.append_latency_row(row)

            self.get_logger().info(
                f"Latency row saved | roundtrip_ms={row['factory_roundtrip_ms']} "
                f"budget_ms={row['available_sort_budget_ms']} "
                f"decision={predicted_binary} class={predicted_class} correct={correct} "
                f"late_for_sort_edge={row['late_for_sort_edge']} "
                f"frame_pick_mode={row['frame_pick_mode']} "
                f"roi_found={row['roi_found']} roi_valid={row.get('roi_valid', '')} roi_fallback={row.get('roi_fallback_used', '')} failure_type={row.get('failure_type', '')}"
            )

        except Exception as e:
            import traceback
            self.get_logger().error(
                f"Request forwarding failed to {UE_FORWARDER_IP}:{UE_FORWARDER_PORT}: {repr(e)}"
            )
            self.get_logger().error(traceback.format_exc())

            self.append_latency_row(
                self.make_base_row(
                    obj_id=obj_id,
                    point_id=point_id,
                    part_name=part_name,
                    expected_label=expected_label,
                    status=f"factory_exception: {repr(e)}",
                    request_run_id=request_run_id,
                    capture_x=capture_x,
                    capture_y=capture_y,
                    capture_target_x=capture_target_x,
                    capture_center_tol=capture_center_tol,
                    stable_ok=stable_ok,
                    fresh_frame_delay_ms=round(fresh_frame_delay_ms, 3) if fresh_frame_delay_ms != "" else "",
                    frame_pick_mode=frame_pick_mode if 'frame_pick_mode' in locals() else "",
                    sort_distance_x=sort_distance_x,
                    time_to_sort_sec=time_to_sort_sec,
                    available_sort_budget_ms=available_sort_budget_ms,
                    late_for_sort_edge="",
                    img_stats=img_stats if 'img_stats' in locals() else None,
                    predicted_decision="unknown",
                    predicted_class="",
                    predicted_confidence=0.0,
                    correct="",
                    bbox="[]",
                    num_detections=0,
                    factory_send_ms=round((send_end_ns - send_start_ns) / 1e6, 3) if send_start_ns and send_end_ns else "",
                    factory_time_to_ack_ms=round((ack_recv_ns - send_start_ns) / 1e6, 3) if send_start_ns and ack_recv_ns else "",
                    factory_roundtrip_ms=round((result_recv_ns - send_start_ns) / 1e6, 3) if send_start_ns and result_recv_ns else "",
                    roi_only=int(roi_meta["roi_only"]) if 'roi_meta' in locals() else "",
                    roi_found=int(roi_meta["roi_found"]) if 'roi_meta' in locals() else "",
                    roi_status=roi_meta["roi_status"] if 'roi_meta' in locals() else "",
                    roi_bbox=json.dumps(roi_meta["roi_bbox"]) if 'roi_meta' in locals() else "[]",
                    full_width=roi_meta["full_width"] if 'roi_meta' in locals() else "",
                    full_height=roi_meta["full_height"] if 'roi_meta' in locals() else "",
                )
            )

        finally:
            try:
                if sock is not None:
                    sock.close()
            except Exception:
                pass


def main():
    rclpy.init()
    node = FactoryRelay()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
