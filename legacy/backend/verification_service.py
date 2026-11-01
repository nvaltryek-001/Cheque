from flask import Flask, request, jsonify
from PIL import Image
import io

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

@app.get("/health")
def health():
    return jsonify({
        "status": "healthy",
        "service": "cheque-verification",
        "model_loaded": False,
        "verification_mode": "safe_fallback"
    }), 200

@app.post("/verify")
def verify():
    uploaded = request.files.get("image")
    if uploaded is None:
        return jsonify({
            "status": "INVALID_REQUEST",
            "error": "Upload an image using the image field"
        }), 400

    try:
        raw = uploaded.read()
        image = Image.open(io.BytesIO(raw))
        image.verify()
    except Exception:
        return jsonify({
            "status": "INVALID_IMAGE",
            "decision": "INDETERMINATE",
            "confidence": None,
            "message": "The uploaded file is not a readable image."
        }), 422

    return jsonify({
        "status": "MODEL_UNAVAILABLE",
        "decision": "INDETERMINATE",
        "confidence": None,
        "verification_type": request.form.get("type", "cheque"),
        "message": (
            "Image received successfully, but no trained verification "
            "model is installed. Authenticity has not been determined."
        ),
        "image": {
            "format": image.format,
            "width": image.width,
            "height": image.height
        }
    }), 200

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8003, debug=False)
