"""
Local Qwen2.5-VL Implementation - For handwritten text extraction
"""
import logging
from typing import Dict, Any
import cv2
import base64
import io

logger = logging.getLogger(__name__)

class VLMExtractorLocal:
    """Qwen2.5-VL for handwritten text extraction."""

    def __init__(self, config):
        self.config = config
        self.enabled = config.ENABLE_QWEN_VLM
        self.model = None
        self.processor = None
        if self.enabled:
            self._initialize_vlm()

    def _initialize_vlm(self):
        """Initialize Qwen2.5-VL model."""
        try:
            logger.info("Initializing Qwen2.5-VL for handwritten text...")
            from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor

            processor = AutoProcessor.from_pretrained("Qwen/Qwen2.5-VL-3B-Instruct")
            model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                "Qwen/Qwen2.5-VL-3B-Instruct",
                torch_dtype="auto",
                device_map="auto"
            )

            self.processor = processor
            self.model = model
            logger.info("✓ Qwen2.5-VL ready")
        except Exception as e:
            logger.error(f"Qwen2.5-VL init failed: {e}")
            self.enabled = False

    def extract(self, image_path: str) -> Dict[str, Any]:
        """Extract handwritten fields from cheque using Qwen."""
        if not self.enabled or self.model is None:
            return {
                'status': 'DISABLED',
                'fields': {
                    'date': '',
                    'payee_name': '',
                    'amount_words': '',
                }
            }

        try:
            logger.info(f"VLM: Extracting handwritten text from {image_path}")

            from PIL import Image

            # Read image
            image = Image.open(image_path).convert('RGB')

            # Prepare prompt for handwritten field extraction
            prompt = """Extract the following handwritten fields from this cheque image:
1. Date (DD/MM/YYYY format)
2. Payee name (who the cheque is made to)
3. Amount in words (e.g., "Two thousand rupees")
4. Any other handwritten text

Respond in JSON format:
{
    "date": "DD/MM/YYYY",
    "payee_name": "Name",
    "amount_words": "Amount",
    "other_text": "Any other handwritten content"
}"""

            # Prepare inputs
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": image},
                        {"type": "text", "text": prompt}
                    ]
                }
            ]

            # Generate response
            text_input = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            image_inputs, video_inputs = self.processor.process_image_input(messages)
            inputs = self.processor(
                text=[text_input],
                images=image_inputs,
                videos=video_inputs,
                padding=True,
                return_tensors="pt",
            )

            # Generate output
            generated_ids = self.model.generate(**inputs, max_new_tokens=256)
            generated_text = self.processor.batch_decode(generated_ids, skip_special_tokens=True)[0]

            logger.info(f"✓ VLM: Generated response - {len(generated_text)} chars")

            # Parse response
            fields = self._parse_vlm_response(generated_text)

            return {
                'status': 'OK',
                'fields': fields,
                'raw_response': generated_text
            }

        except Exception as e:
            logger.error(f"VLM error: {e}")
            return {
                'status': 'ERROR',
                'error': str(e),
                'fields': {}
            }

    def _parse_vlm_response(self, response: str) -> Dict[str, str]:
        """Parse Qwen response to extract fields."""
        try:
            import json
            import re

            # Try to extract JSON from response
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return {
                    'date': data.get('date', '').strip(),
                    'payee_name': data.get('payee_name', '').strip(),
                    'amount_words': data.get('amount_words', '').strip(),
                    'other_text': data.get('other_text', '').strip()
                }
        except:
            pass

        # Fallback: simple text extraction
        return {
            'date': '',
            'payee_name': '',
            'amount_words': '',
            'other_text': response
        }
