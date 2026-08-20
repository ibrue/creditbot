"""Face detection + recognition using OpenCV's YuNet and SFace models.

Both models are small ONNX files that run on CPU — no GPU, no dlib,
no compilation. Download them once with download_models.py.
"""
import os

import cv2
import numpy as np

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
DETECT_MODEL = os.path.join(MODELS_DIR, "face_detection_yunet_2023mar.onnx")
RECOGNIZE_MODEL = os.path.join(MODELS_DIR, "face_recognition_sface_2021dec.onnx")

# Official cosine-similarity threshold for SFace (from the OpenCV docs).
# Higher score = more similar. A match requires score >= threshold.
COSINE_THRESHOLD = 0.363

# Minimum face box size (pixels) — ignores small/far-away faces so the
# kiosk only reacts to the person standing in front of it.
MIN_FACE_SIZE = 90


class FaceEngine:
    def __init__(self):
        for path in (DETECT_MODEL, RECOGNIZE_MODEL):
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"Model not found: {path}\n"
                    "Run:  python download_models.py"
                )
        self.detector = cv2.FaceDetectorYN_create(
            DETECT_MODEL, "", (320, 320), score_threshold=0.8
        )
        self.recognizer = cv2.FaceRecognizerSF_create(RECOGNIZE_MODEL, "")

    def detect_best_face(self, frame_bgr):
        """Detect the largest face in the frame.

        Returns the raw face row (box + landmarks + score) or None.
        """
        h, w = frame_bgr.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(frame_bgr)
        if faces is None or len(faces) == 0:
            return None

        # Pick the largest face
        best = max(faces, key=lambda f: f[2] * f[3])
        if best[2] < MIN_FACE_SIZE or best[3] < MIN_FACE_SIZE:
            return None
        return best

    @staticmethod
    def yaw_ratio(face_row) -> float:
        """Estimate horizontal head turn from YuNet's landmarks.

        Returns the nose offset from the eye midpoint, normalized by the
        eye distance: ~0 facing the camera, roughly ±0.3+ when the head is
        clearly turned to a side. Used for the liveness check and for
        pose-guided enrollment.
        """
        right_eye_x, left_eye_x = float(face_row[4]), float(face_row[6])
        nose_x = float(face_row[8])
        eye_dist = abs(left_eye_x - right_eye_x)
        if eye_dist < 1.0:
            return 0.0
        mid_x = (left_eye_x + right_eye_x) / 2.0
        return (nose_x - mid_x) / eye_dist

    @staticmethod
    def crop_face(frame_bgr, face_row, margin: float = 0.35):
        """Cut the face out of the frame with some margin around it.

        Returns a BGR image — used for the local face capture log.
        """
        fh, fw = frame_bgr.shape[:2]
        x, y, w, h = [int(v) for v in face_row[:4]]
        mx, my = int(w * margin), int(h * margin)
        x1, y1 = max(0, x - mx), max(0, y - my)
        x2, y2 = min(fw, x + w + mx), min(fh, y + h + my)
        return frame_bgr[y1:y2, x1:x2].copy()

    def embed(self, frame_bgr, face_row):
        """Compute a normalized 128-d SFace embedding for a detected face."""
        aligned = self.recognizer.alignCrop(frame_bgr, face_row)
        feature = self.recognizer.feature(aligned)
        vec = np.asarray(feature).flatten().astype(np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    @staticmethod
    def match(embedding, known_faces):
        """Match an embedding against enrolled faces.

        known_faces: list of dicts with keys discord_id, name, embedding.
        Returns (discord_id, name, score) of the best match at or above the
        threshold, or (None, None, best_score).
        """
        best_score = -1.0
        best_id, best_name = None, None
        query = np.asarray(embedding, dtype=np.float32)

        for face in known_faces:
            candidate = np.asarray(face["embedding"], dtype=np.float32)
            norm = np.linalg.norm(candidate)
            if norm > 0:
                candidate = candidate / norm
            score = float(np.dot(query, candidate))
            if score > best_score:
                best_score = score
                best_id = face["discord_id"]
                best_name = face["name"]

        if best_score >= COSINE_THRESHOLD:
            return best_id, best_name, best_score
        return None, None, best_score
