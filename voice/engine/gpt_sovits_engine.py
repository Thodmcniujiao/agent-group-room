import urllib.request
import urllib.parse
import json
from pathlib import Path
from typing import Dict, Any, Optional
from .base import BaseTTSEngine

class GPTSoVITSEngine(BaseTTSEngine):
    """
    Offline Deep Learning TTS Engine via GPT-SoVITS API.
    Utilizes local RTX 5080 GPU for high-fidelity voice cloning.
    """

    def __init__(self, api_base: str = "http://127.0.0.1:9880"):
        self.api_base = api_base.rstrip("/")
        self._last_loaded_character: Optional[str] = None

    def is_available(self) -> bool:
        """Check if local GPT-SoVITS API server is listening."""
        try:
            req = urllib.request.Request(f"{self.api_base}/", method="GET")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                return resp.status in (200, 404, 405)
        except Exception:
            return False

    def _switch_character_weights(self, character_config: Dict[str, Any], root_dir: Path):
        sovits_cfg = character_config.get("gpt_sovits", {})
        sovits_rel = sovits_cfg.get("sovits_path", "")
        gpt_rel = sovits_cfg.get("gpt_path", "")

        sovits_abs = str((root_dir / sovits_rel).resolve())
        gpt_abs = str((root_dir / gpt_rel).resolve())

        # Call API switch endpoints if weights exist
        try:
            if Path(sovits_abs).exists():
                url = f"{self.api_base}/set_sovits_weights?weights_path={urllib.parse.quote(sovits_abs)}"
                urllib.request.urlopen(url, timeout=3.0)
            if Path(gpt_abs).exists():
                url = f"{self.api_base}/set_gpt_weights?weights_path={urllib.parse.quote(gpt_abs)}"
                urllib.request.urlopen(url, timeout=3.0)
        except Exception as e:
            print(f"[GPTSoVITSEngine] Note on weights switch: {e}")

    async def synthesize(
        self,
        character_id: str,
        character_config: Dict[str, Any],
        text: str,
        output_path: Path,
        speed: float = 1.0,
        pitch_offset: int = 0
    ) -> bool:
        if not self.is_available():
            raise ConnectionError(
                f"GPT-SoVITS local server at {self.api_base} is not reachable. "
                "Please start the GPT-SoVITS API service on your RTX 5080."
            )

        root_dir = output_path.parent.parent
        if self._last_loaded_character != character_id:
            self._switch_character_weights(character_config, root_dir)
            self._last_loaded_character = character_id

        sovits_cfg = character_config.get("gpt_sovits", {})
        ref_audio_rel = sovits_cfg.get("ref_audio", f"characters/{character_id}/sample.mp3")
        ref_audio_abs = str((root_dir / ref_audio_rel).resolve())
        prompt_text = sovits_cfg.get("prompt_text", "你好。")
        prompt_lang = sovits_cfg.get("prompt_lang", "zh")

        payload = {
            "text": text,
            "text_lang": "zh",
            "ref_audio_path": ref_audio_abs,
            "prompt_text": prompt_text,
            "prompt_lang": prompt_lang,
            "top_k": 5,
            "top_p": 1.0,
            "temperature": 1.0,
            "speed": speed
        }

        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.api_base}/tts",
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            audio_bytes = resp.read()
            with open(output_path, "wb") as f:
                f.write(audio_bytes)

        return output_path.exists() and output_path.stat().st_size > 0
