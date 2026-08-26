from __future__ import annotations

import base64
import io
import os

import cv2
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageGrab
from pydantic import BaseModel

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

if GOOGLE_API_KEY:
    API_KEY = GOOGLE_API_KEY
    API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    MODEL = "gemini-2.5-flash"
elif OPENROUTER_API_KEY:
    API_KEY = OPENROUTER_API_KEY
    API_URL = "https://openrouter.ai/api/v1/chat/completions"
    MODEL = "google/gemma-4-31b-it:free"
else:
    raise RuntimeError("Set GOOGLE_API_KEY or OPENROUTER_API_KEY in lyra-vision/.env")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SeeRequest(BaseModel):
    source: str = "screen"
    prompt: str = "Describe what you see."


def _capture_webcam() -> Image.Image | None:
    cap = cv2.VideoCapture(0)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return None
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def _to_base64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@app.post("/see")
def see(req: SeeRequest):
    if req.source not in ("screen", "webcam"):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid source '{req.source}'. Use 'screen' or 'webcam'.",
        )

    if req.source == "screen":
        img = ImageGrab.grab()
    else:
        img = _capture_webcam()
        if img is None:
            return {"description": "Error: could not access webcam"}

    b64 = _to_base64(img)

    try:
        resp = httpx.post(
            API_URL,
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={
                "model": MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64}"},
                            },
                            {"type": "text", "text": req.prompt},
                        ],
                    }
                ],
            },
            timeout=30,
        )
        resp.raise_for_status()
        return {"description": resp.json()["choices"][0]["message"]["content"]}
    except httpx.HTTPStatusError as e:
        try:
            detail = e.response.json().get("error", {}).get("message", str(e))
        except Exception:
            detail = str(e)
        return {"description": f"Error: vision model rejected — {detail}"}
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        return {"description": f"Error: vision provider unreachable — {e}"}
    except Exception as e:
        return {"description": f"Error: vision request failed — {e}"}
