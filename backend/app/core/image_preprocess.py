import cv2
import numpy as np
from PIL import Image
from typing import Tuple, Optional
import os


def detect_blur(image_path: str, threshold: float = 100.0) -> bool:
    img = cv2.imread(image_path)
    if img is None:
        return True
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    return laplacian_var < threshold


def auto_crop(image_path: str, output_path: str) -> str:
    img = cv2.imread(image_path)
    if img is None:
        return image_path
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blurred, 75, 200)
    contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return image_path
    largest = max(contours, key=cv2.contourArea)
    epsilon = 0.02 * cv2.arcLength(largest, True)
    approx = cv2.approxPolyDP(largest, epsilon, True)
    if len(approx) == 4:
        pts = _order_points(approx.reshape(4, 2))
        warped = _four_point_transform(img, pts)
        cv2.imwrite(output_path, warped)
        return output_path
    return image_path


def correct_skew(image_path: str, output_path: str) -> str:
    img = cv2.imread(image_path)
    if img is None:
        return image_path
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100, minLineLength=100, maxLineGap=10)
    if lines is None:
        return image_path
    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle = np.arctan2(y2 - y1, x2 - x1) * 180 / np.pi
        angles.append(angle)
    if not angles:
        return image_path
    median_angle = np.median(angles)
    if abs(median_angle) < 0.5:
        return image_path
    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, median_angle, 1.0)
    rotated = cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    cv2.imwrite(output_path, rotated)
    return output_path


def generate_thumbnail(image_path: str, output_path: str, size: Tuple[int, int] = (300, 400)):
    img = Image.open(image_path)
    img.thumbnail(size, Image.LANCZOS)
    img.save(output_path)


def preprocess(image_path: str, output_dir: str) -> Tuple[str, Optional[str]]:
    if detect_blur(image_path):
        return image_path, "照片模糊，请重新拍摄"

    basename = os.path.splitext(os.path.basename(image_path))[0]
    crop_path = os.path.join(output_dir, f"{basename}_crop.jpg")
    skew_path = os.path.join(output_dir, f"{basename}_skew.jpg")
    thumb_path = os.path.join(output_dir, f"{basename}_thumb.jpg")

    processed = auto_crop(image_path, crop_path)
    processed = correct_skew(processed, skew_path)
    generate_thumbnail(processed, thumb_path)

    return processed, None


def _order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def _four_point_transform(image, pts):
    rect = _order_points(pts)
    (tl, tr, br, bl) = rect
    widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    maxWidth = max(int(widthA), int(widthB))
    heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    maxHeight = max(int(heightA), int(heightB))
    dst = np.array([[0, 0], [maxWidth - 1, 0], [maxWidth - 1, maxHeight - 1], [0, maxHeight - 1]], dtype="float32")
    M = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image, M, (maxWidth, maxHeight))
