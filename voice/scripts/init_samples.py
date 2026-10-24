import asyncio
import json
from pathlib import Path
import edge_tts

ROOT = Path(__file__).resolve().parent.parent
CHARACTERS_DIR = ROOT / "characters"

async def generate_samples():
    for char_id in ["chatgpt", "claude", "gemini", "deepseek"]:
        char_dir = CHARACTERS_DIR / char_id
        config_file = char_dir / "character.json"
        if not config_file.exists():
            continue
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        quote = cfg.get("default_quote", "你好！")
        tts_cfg = cfg.get("edge_tts", {})
        voice = tts_cfg.get("voice", "zh-CN-XiaoxiaoNeural")
        rate = tts_cfg.get("rate", "+0%")
        pitch = tts_cfg.get("pitch", "+0Hz")

        out_sample = char_dir / "sample.mp3"
        print(f"Generating sample for {cfg['name']} ({voice})...")
        comm = edge_tts.Communicate(text=quote, voice=voice, rate=rate, pitch=pitch)
        await comm.save(str(out_sample))
        print(f" -> Saved to {out_sample.name}")

if __name__ == "__main__":
    asyncio.run(generate_samples())
