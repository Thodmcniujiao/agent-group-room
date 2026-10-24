import os
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from .edge_engine import EdgeEngine
from .gpt_sovits_engine import GPTSoVITSEngine

class VoiceRouter:
    """Central router managing character sound libraries and TTS synthesis."""

    def __init__(self, root_dir: Optional[Path] = None):
        if root_dir is None:
            self.root_dir = Path(__file__).resolve().parent.parent
        else:
            self.root_dir = Path(root_dir)

        self.characters_dir = self.root_dir / "characters"
        self.output_dir = self.root_dir / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.edge_engine = EdgeEngine()
        self.gpt_engine = GPTSoVITSEngine()

    def list_characters(self) -> List[Dict[str, Any]]:
        """List all available characters and their personas."""
        characters = []
        for char_dir in self.characters_dir.iterdir():
            if char_dir.is_dir():
                cfg_file = char_dir / "character.json"
                if cfg_file.exists():
                    try:
                        with open(cfg_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            has_sample = (char_dir / "sample.mp3").exists()
                            data["has_sample"] = has_sample
                            characters.append(data)
                    except Exception as e:
                        print(f"Error loading {cfg_file}: {e}")
        # Sort in predefined order: chatgpt, claude, gemini, deepseek
        order = {"chatgpt": 0, "claude": 1, "gemini": 2, "deepseek": 3}
        characters.sort(key=lambda c: order.get(c.get("id"), 99))
        return characters

    def get_character(self, character_id: str) -> Optional[Dict[str, Any]]:
        char_file = self.characters_dir / character_id / "character.json"
        if not char_file.exists():
            return None
        with open(char_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def check_engine_status(self) -> Dict[str, Any]:
        """Check availability of TTS engines."""
        return {
            "edge": {
                "available": self.edge_engine.is_available(),
                "description": "内置快速合成引擎（开箱即用，已调校四大 AI 娘专属预设）"
            },
            "gpt_sovits": {
                "available": self.gpt_engine.is_available(),
                "api_base": self.gpt_engine.api_base,
                "description": "RTX 5080 离线大模型深度克隆引擎（GPT-SoVITS 服务）"
            }
        }

    async def speak(
        self,
        character_id: str,
        text: str,
        engine_type: str = "auto",
        speed: float = 1.0,
        pitch_offset: int = 0
    ) -> Dict[str, Any]:
        char_cfg = self.get_character(character_id)
        if not char_cfg:
            raise ValueError(f"Unknown character: {character_id}")

        timestamp = int(time.time() * 1000)
        filename = f"{character_id}_{timestamp}.mp3"
        out_file = self.output_dir / filename

        used_engine = "edge"
        if engine_type == "gpt_sovits":
            used_engine = "gpt_sovits"
            await self.gpt_engine.synthesize(
                character_id=character_id,
                character_config=char_cfg,
                text=text,
                output_path=out_file,
                speed=speed,
                pitch_offset=pitch_offset
            )
        elif engine_type == "auto":
            if self.gpt_engine.is_available():
                try:
                    used_engine = "gpt_sovits"
                    await self.gpt_engine.synthesize(
                        character_id=character_id,
                        character_config=char_cfg,
                        text=text,
                        output_path=out_file,
                        speed=speed,
                        pitch_offset=pitch_offset
                    )
                except Exception as e:
                    print(f"GPT-SoVITS fallback to edge: {e}")
                    used_engine = "edge"
                    await self.edge_engine.synthesize(
                        character_id=character_id,
                        character_config=char_cfg,
                        text=text,
                        output_path=out_file,
                        speed=speed,
                        pitch_offset=pitch_offset
                    )
            else:
                used_engine = "edge"
                await self.edge_engine.synthesize(
                    character_id=character_id,
                    character_config=char_cfg,
                    text=text,
                    output_path=out_file,
                    speed=speed,
                    pitch_offset=pitch_offset
                )
        else: # force edge
            used_engine = "edge"
            await self.edge_engine.synthesize(
                character_id=character_id,
                character_config=char_cfg,
                text=text,
                output_path=out_file,
                speed=speed,
                pitch_offset=pitch_offset
            )

        return {
            "success": True,
            "character_id": character_id,
            "character_name": char_cfg.get("name"),
            "used_engine": used_engine,
            "filename": filename,
            "file_path": str(out_file),
            "file_size": out_file.stat().st_size if out_file.exists() else 0,
            "url": f"/audio/output/{filename}"
        }
