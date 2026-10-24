import sys
import argparse
import asyncio
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from engine.router import VoiceRouter

def main():
    parser = argparse.ArgumentParser(description="AI 娘文字转语音命令行工具")
    parser.add_argument("--role", "-r", choices=["chatgpt", "claude", "gemini", "deepseek"], default="chatgpt", help="发音角色")
    parser.add_argument("--text", "-t", required=True, help="需要转换的文本")
    parser.add_argument("--engine", "-e", choices=["auto", "edge", "gpt_sovits"], default="auto", help="发音引擎")
    parser.add_argument("--speed", "-s", type=float, default=1.0, help="语速倍率 (默认 1.0)")
    parser.add_argument("--pitch", "-p", type=int, default=0, help="音调偏移 Hz (默认 0)")
    parser.add_argument("--out", "-o", default=None, help="输出音频文件路径")
    args = parser.parse_args()

    router = VoiceRouter(root_dir=ROOT_DIR)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    print(f"[*] 正在为 [{args.role}] 生成语音: \"{args.text}\" ...")
    result = loop.run_until_complete(
        router.speak(
            character_id=args.role,
            text=args.text,
            engine_type=args.engine,
            speed=args.speed,
            pitch_offset=args.pitch
        )
    )

    file_path = result["file_path"]
    if args.out:
        target_path = Path(args.out).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copyfile(file_path, target_path)
        print(f"[OK] 生成成功，保存至: {target_path}")
    else:
        print(f"[OK] 生成成功！")
        print(f"    角色: {result['character_name']}")
        print(f"    引擎: {result['used_engine']}")
        print(f"    文件: {file_path}")

if __name__ == "__main__":
    main()
