import unittest
from io import BytesIO

from PIL import Image

from app.drowsiness.detector import (
    DrowsinessDetector,
    InvalidFrameError,
    _closed_probability,
)
from app.drowsiness.monitor import DrowsinessMonitor


class DrowsinessMonitorTests(unittest.TestCase):
    def test_closed_label_uses_model_probability(self):
        self.assertEqual(
            _closed_probability(
                [{"label": "closeEye", "score": 0.83}, {"label": "openEye", "score": 0.17}]
            ),
            0.83,
        )

    def test_open_label_inverts_model_probability(self):
        self.assertAlmostEqual(
            _closed_probability([{"label": "openEye", "score": 0.72}]),
            0.28,
        )

    def test_long_eye_closure_triggers_alarm(self):
        monitor = DrowsinessMonitor()
        monitor.update(0.0, 0.9)
        result = monitor.update(0.9, 0.9)

        self.assertEqual(result.status, "drowsy")
        self.assertTrue(result.alarm)
        self.assertAlmostEqual(result.closed_duration, 0.9)

    def test_high_perclos_triggers_alarm_after_window_fills(self):
        monitor = DrowsinessMonitor()
        for timestamp in range(5):
            result = monitor.update(float(timestamp), 0.9 if timestamp < 2 else 0.1)

        self.assertTrue(result.alarm)
        self.assertAlmostEqual(result.perclos, 0.5)

    def test_missing_face_resets_window_and_closure(self):
        monitor = DrowsinessMonitor()
        monitor.update(0.0, 0.9)
        result = monitor.update(1.0, None)

        self.assertEqual(result.status, "no_face")
        self.assertFalse(result.alarm)
        self.assertEqual(result.perclos, 0.0)


class DrowsinessDetectorTests(unittest.TestCase):
    def test_rejects_invalid_image_data(self):
        detector = DrowsinessDetector()

        with self.assertRaisesRegex(InvalidFrameError, "not a valid image"):
            detector.analyze("invalid-image", b"not an image")

    def test_rejects_oversized_image_dimensions(self):
        image = Image.new("RGB", (2000, 1600))
        image_bytes = BytesIO()
        image.save(image_bytes, format="JPEG")
        detector = DrowsinessDetector()

        with self.assertRaisesRegex(InvalidFrameError, "3 megapixels"):
            detector.analyze("oversized-image", image_bytes.getvalue())

    def test_reports_missing_face_without_loading_the_model(self):
        image = Image.new("RGB", (320, 240), "black")
        image_bytes = BytesIO()
        image.save(image_bytes, format="JPEG")
        detector = DrowsinessDetector()

        result = detector.analyze("no-face", image_bytes.getvalue())

        self.assertEqual(result["status"], "no_face")
        self.assertIsNone(detector._classifier)


if __name__ == "__main__":
    unittest.main()
