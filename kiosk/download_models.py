"""Download the ONNX face models from the OpenCV model zoo (one-time setup)."""
import os
import urllib.request

# opencv_zoo stores models in Git LFS, so we fetch from the LFS media host
# (plain raw.githubusercontent.com URLs return tiny pointer files instead).
MODELS = {
    "face_detection_yunet_2023mar.onnx":
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
        "models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx":
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
        "models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}

models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
os.makedirs(models_dir, exist_ok=True)

for filename, url in MODELS.items():
    dest = os.path.join(models_dir, filename)
    if os.path.exists(dest) and os.path.getsize(dest) > 100_000:
        print(f"✓ {filename} already downloaded")
        continue
    print(f"Downloading {filename} ...")
    request = urllib.request.Request(url, headers={"User-Agent": "creditbot-kiosk"})
    with urllib.request.urlopen(request) as response, open(dest, "wb") as f:
        f.write(response.read())
    size = os.path.getsize(dest)
    if size < 100_000:
        os.remove(dest)
        raise RuntimeError(f"{filename} download looks wrong ({size} bytes)")
    print(f"✓ Saved to {dest} ({size // 1024} KB)")

print("Done! You can now run: python kiosk.py")
