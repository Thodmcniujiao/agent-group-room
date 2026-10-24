import asyncio
from pathlib import Path
from typing import Dict, Any
import edge_tts
from .base import BaseTTSEngine

class EdgeEngine(BaseTTSEngine):
    """
    Fast, lightweight, preset-based TTS engine.
    Uses tuned parameters to mimic the persona of the 4 AI girls.
    """

    def is_available(self) -> bool:
        return True

    async def synthesize(
        self,
        character_id: str,
        character_config: Dict[str, Any],
        text: str,
        output_path: Path,
        speed: float = 1.0,
        pitch_offset: int = 0
    ) -> bool:
        tts_cfg = character_config.get("edge_tts", {})
        voice = tts_cfg.get("voice", "zh-CN-XiaoxiaoNeural")

        # Calculate dynamic rate
        base_rate_str = tts_cfg.get("rate", "+0%")
        base_rate = int(base_rate_str.replace("%", "").replace("+", ""))
        target_rate = base_rate + int((speed - 1.0) * 100)
        rate_str = f"{target_rate:+d}%"

        # Calculate dynamic pitch
        base_pitch_str = tts_cfg.get("pitch", "+0Hz")
        base_pitch = int(base_pitch_str.replace("Hz", "").replace("+", ""))
        target_pitch = base_pitch + pitch_offset
        pitch_str = f"{target_pitch:+d}Hz"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        comm = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=rate_str,
            pitch=pitch_str
        )
        await comm.save(str(output_path))
        return output_path.exists() and output_path.stat().st_size > 0
