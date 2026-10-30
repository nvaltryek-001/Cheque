from config import Config

print("QWEN ENABLED:", Config.ENABLE_QWEN_VLM)
print("PADDLE OCR ENABLED:", Config.ENABLE_PADDLE_OCR)

try:
    import transformers
    print("TRANSFORMERS:", transformers.__version__)
except Exception as e:
    print("TRANSFORMERS ERROR:", repr(e))

try:
    import torch
    print("TORCH:", torch.__version__)
    print("CUDA AVAILABLE:", torch.cuda.is_available())
except Exception as e:
    print("TORCH ERROR:", repr(e))
