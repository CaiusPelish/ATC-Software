import os
import threading
import time
from dataclasses import asdict, dataclass
from io import BytesIO

from .monitor import DrowsinessMonitor


MODEL_ID = "dima806/closed_eyes_image_detection"
SESSION_TIMEOUT_SECONDS = 15 * 60
MAX_IMAGE_PIXELS = 3_000_000


class InvalidFrameError(ValueError):
    pass


class ModelUnavailableError(RuntimeError):
    pass


@dataclass
class _Session:
    monitor: DrowsinessMonitor
    last_seen: float


def _closed_probability(predictions: list[dict]) -> float:
    for prediction in predictions:
        if "close" in prediction["label"].lower():
            return float(prediction["score"])

    for prediction in predictions:
        if "open" in prediction["label"].lower():
            return 1.0 - float(prediction["score"])

    return 0.0


class DrowsinessDetector:
    def __init__(self) -> None:
        self.model_id = os.environ.get("DROWSINESS_MODEL_ID", MODEL_ID)
        self._classifier = None
        self._face_cascade = None
        self._eye_cascade = None
        self._sessions: dict[str, _Session] = {}
        self._sessions_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    def reset(self, session_id: str) -> None:
        with self._sessions_lock:
            self._sessions.pop(session_id, None)

    def analyze(self, session_id: str, image_bytes: bytes) -> dict:
        import cv2
        import numpy as np
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(BytesIO(image_bytes)) as source:
                if source.format not in {"JPEG", "PNG"}:
                    raise InvalidFrameError("Upload a JPEG or PNG camera frame.")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise InvalidFrameError(
                        "Camera frames must be 3 megapixels or smaller."
                    )
                rgb = np.asarray(source.convert("RGB"))
        except (Image.DecompressionBombError, OSError, UnidentifiedImageError) as exc:
            raise InvalidFrameError("The uploaded file is not a valid image.") from exc

        if rgb.size == 0:
            raise InvalidFrameError("The uploaded file is not a valid image.")
        image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

        face_box, crops = self._find_eye_crops(image, cv2, Image)
        now = time.monotonic()
        session = self._get_session(session_id, now)

        if face_box is None:
            result = session.monitor.update(now, None)
        else:
            with self._inference_lock:
                classifier = self._get_classifier()
                predictions = classifier(crops)

            crop_predictions_list = (
                [predictions]
                if predictions and isinstance(predictions[0], dict)
                else predictions
            )
            closed_probabilities = [
                _closed_probability(crop_predictions)
                for crop_predictions in crop_predictions_list
            ]
            closed_probability = sum(closed_probabilities) / len(closed_probabilities)
            result = session.monitor.update(now, closed_probability)

        session.last_seen = now
        return asdict(result)

    def _get_session(self, session_id: str, now: float) -> _Session:
        with self._sessions_lock:
            expired = [
                key
                for key, session in self._sessions.items()
                if now - session.last_seen > SESSION_TIMEOUT_SECONDS
            ]
            for key in expired:
                del self._sessions[key]

            session = self._sessions.get(session_id)
            if session is None:
                session = _Session(DrowsinessMonitor(), now)
                self._sessions[session_id] = session
            return session

    def _get_classifier(self):
        if self._classifier is None:
            try:
                import torch
                from transformers import pipeline

                device = 0 if torch.cuda.is_available() else -1
                self._classifier = pipeline(
                    "image-classification",
                    model=self.model_id,
                    device=device,
                    top_k=None,
                )
            except Exception as exc:
                raise ModelUnavailableError(
                    f"Could not load Hugging Face model '{self.model_id}': {exc}"
                ) from exc
        return self._classifier

    def _find_eye_crops(self, image, cv2, image_class):
        if self._face_cascade is None or self._eye_cascade is None:
            cascade_directory = cv2.data.haarcascades
            self._face_cascade = cv2.CascadeClassifier(
                os.path.join(cascade_directory, "haarcascade_frontalface_default.xml")
            )
            self._eye_cascade = cv2.CascadeClassifier(
                os.path.join(cascade_directory, "haarcascade_eye.xml")
            )
            if self._face_cascade.empty() or self._eye_cascade.empty():
                raise ModelUnavailableError("OpenCV Haar cascades could not be loaded.")

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        faces = self._face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.2,
            minNeighbors=5,
            minSize=(80, 80),
        )
        if len(faces) == 0:
            return None, []

        x, y, width, height = max(faces, key=lambda box: box[2] * box[3])
        upper_face = gray[y : y + int(height * 0.6), x : x + width]
        eyes = self._eye_cascade.detectMultiScale(
            upper_face,
            scaleFactor=1.1,
            minNeighbors=6,
            minSize=(max(1, width // 8), max(1, width // 8)),
        )

        crops = []
        for eye_x, eye_y, eye_width, eye_height in sorted(
            eyes,
            key=lambda box: box[2] * box[3],
            reverse=True,
        )[:2]:
            global_x, global_y = x + eye_x, y + eye_y
            padding = int(eye_width * 0.15)
            left = max(global_x - padding, 0)
            top = max(global_y - padding, 0)
            right = min(global_x + eye_width + padding, rgb.shape[1])
            bottom = min(global_y + eye_height + padding, rgb.shape[0])
            if right > left and bottom > top:
                crops.append(image_class.fromarray(rgb[top:bottom, left:right]))

        if not crops:
            top = y + int(height * 0.20)
            bottom = y + int(height * 0.55)
            crops.append(image_class.fromarray(rgb[top:bottom, x : x + width]))

        return (x, y, width, height), crops
