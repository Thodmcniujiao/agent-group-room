import os
import sys
import json
import time
import shutil
import asyncio
import subprocess
import urllib.request
from pathlib import Path
from bottle import Bottle, request, response, static_file, run

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from engine.router import VoiceRouter

app = Bottle()
router = VoiceRouter(root_dir=ROOT_DIR)

ENGINE_SUBDIR = ROOT_DIR / "GPT-SoVITS-Engine" / "GPT-SoVITS-v2pro-20250604-nvidia50"
PYTHON_RUNTIME = ENGINE_SUBDIR / "runtime" / "python.exe"
WEBUI_SCRIPT = ENGINE_SUBDIR / "webui.py"

# Training process holder
_training_process = None

@app.hook('after_request')
def enable_cors():
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Origin, Accept, Content-Type, X-Requested-With'

# ----------------- 静态页面与音频 -----------------

@app.route('/')
def index():
    return static_file('index.html', root=str(ROOT_DIR / 'web'))

@app.route('/api/characters')
def get_characters():
    response.content_type = 'application/json; charset=utf-8'
    return json.dumps(router.list_characters(), ensure_ascii=False)

@app.route('/api/status')
def get_status():
    status = router.check_engine_status()
    # Add training webui status
    status["training_webui"] = {
        "available": is_port_open("127.0.0.1", 9874),
        "port": 9874,
        "url": "http://127.0.0.1:9874",
        "is_running": is_training_running()
    }
    response.content_type = 'application/json; charset=utf-8'
    return json.dumps(status, ensure_ascii=False)

@app.route('/audio/sample/<char_id>')
def serve_sample(char_id):
    char_dir = ROOT_DIR / 'characters' / char_id
    sample_file = char_dir / 'sample.mp3'
    if sample_file.exists():
        return static_file('sample.mp3', root=str(char_dir), mimetype='audio/mpeg')
    response.status = 404
    return "Sample not found"

@app.route('/audio/output/<filename>')
def serve_output(filename):
    return static_file(filename, root=str(ROOT_DIR / 'output'), mimetype='audio/mpeg')

# ----------------- 发音合成 -----------------

@app.route('/api/tts', method=['POST', 'OPTIONS'])
def api_tts():
    if request.method == 'OPTIONS':
        return {}
    try:
        data = request.json or {}
        char_id = data.get('character_id', 'chatgpt')
        text = data.get('text', '').strip()
        engine_type = data.get('engine', 'auto')
        speed = float(data.get('speed', 1.0))
        pitch_offset = int(data.get('pitch', 0))

        if not text:
            response.status = 400
            return {"success": False, "error": "文本内容不能为空"}

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(
            router.speak(
                character_id=char_id,
                text=text,
                engine_type=engine_type,
                speed=speed,
                pitch_offset=pitch_offset
            )
        )
        response.content_type = 'application/json; charset=utf-8'
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        response.status = 500
        return {"success": False, "error": str(e)}

# ----------------- 训练控制台管理 -----------------

def is_port_open(host: str, port: int) -> bool:
    try:
        req = urllib.request.Request(f"http://{host}:{port}/", method="GET")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            return resp.status in (200, 302, 404)
    except Exception:
        return False

def is_training_running() -> bool:
    global _training_process
    if _training_process is not None and _training_process.poll() is None:
        return True
    return is_port_open("127.0.0.1", 9874)

@app.route('/api/training/toggle', method=['POST', 'OPTIONS'])
def toggle_training():
    response.content_type = 'application/json; charset=utf-8'
    if request.method == 'OPTIONS':
        return "{}"
    global _training_process
    try:
        data = request.json or {}
        action = data.get('action', 'start') # 'start' or 'stop'

        if action == 'start':
            if is_training_running():
                return json.dumps({"success": True, "message": "训练控制台已在运行中", "url": "http://127.0.0.1:9874"}, ensure_ascii=False)
            if not PYTHON_RUNTIME.exists() or not WEBUI_SCRIPT.exists():
                return json.dumps({"success": False, "error": "未找到训练引擎或 runtime 解释器"}, ensure_ascii=False)

            cmd = [str(PYTHON_RUNTIME), "-I", str(WEBUI_SCRIPT), "zh_CN"]
            env = os.environ.copy()
            env["PATH"] = f"{ENGINE_SUBDIR / 'runtime'};{env.get('PATH', '')}"

            _training_process = subprocess.Popen(
                cmd,
                cwd=str(ENGINE_SUBDIR),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return json.dumps({"success": True, "message": "训练引擎正在启动，请稍候...", "url": "http://127.0.0.1:9874"}, ensure_ascii=False)
        else:
            if _training_process and _training_process.poll() is None:
                _training_process.terminate()
                _training_process = None
            # Kill remaining if port still open
            subprocess.run(["taskkill", "/F", "/IM", "python.exe", "/FI", "WINDOWTITLE eq *webui*"], capture_output=True)
            return json.dumps({"success": True, "message": "已发送停止指令"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

# ----------------- 数据集管理 -----------------

@app.route('/api/dataset/status')
def dataset_status():
    dataset_dir = ROOT_DIR / "dataset"
    results = {}
    for role in ["chatgpt", "claude", "gemini", "deepseek"]:
        r_dir = dataset_dir / role
        if not r_dir.exists():
            r_dir.mkdir(parents=True, exist_ok=True)
        files = [f.name for f in r_dir.iterdir() if f.suffix.lower() in ('.wav', '.mp3', '.ogg', '.flac', '.m4a')]
        results[role] = {
            "count": len(files),
            "files": files[:10],
            "dir_path": str(r_dir)
        }
    response.content_type = 'application/json; charset=utf-8'
    return json.dumps(results, ensure_ascii=False)

@app.route('/api/dataset/open', method=['POST', 'OPTIONS'])
def open_dataset():
    if request.method == 'OPTIONS':
        return {}
    data = request.json or {}
    role = data.get('role', 'deepseek')
    target_dir = ROOT_DIR / "dataset" / role
    target_dir.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(["explorer.exe", str(target_dir)])
    return {"success": True, "message": f"已在电脑中打开 {role} 数据集文件夹"}

# ----------------- 训练模型扫描与绑定 -----------------

@app.route('/api/models/scan')
def scan_models():
    """Scan trained weights inside GPT-SoVITS-Engine and voice/models."""
    models_dir = ROOT_DIR / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    weights = {"sovits": [], "gpt": []}

    # Scan models directory
    for f in models_dir.glob("*.pth"):
        weights["sovits"].append({"name": f.name, "path": str(f.relative_to(ROOT_DIR))})
    for f in models_dir.glob("*.ckpt"):
        weights["gpt"].append({"name": f.name, "path": str(f.relative_to(ROOT_DIR))})

    # Scan Engine weights folders
    for s_folder in ENGINE_SUBDIR.glob("SoVITS_weights*"):
        if s_folder.is_dir():
            for f in s_folder.glob("*.pth"):
                weights["sovits"].append({"name": f"{s_folder.name}/{f.name}", "path": str(f.relative_to(ROOT_DIR))})

    for g_folder in ENGINE_SUBDIR.glob("GPT_weights*"):
        if g_folder.is_dir():
            for f in g_folder.glob("*.ckpt"):
                weights["gpt"].append({"name": f"{g_folder.name}/{f.name}", "path": str(f.relative_to(ROOT_DIR))})

    response.content_type = 'application/json; charset=utf-8'
    return json.dumps(weights, ensure_ascii=False)

@app.route('/api/models/bind', method=['POST', 'OPTIONS'])
def bind_model():
    if request.method == 'OPTIONS':
        return {}
    data = request.json or {}
    role = data.get('role')
    sovits_path = data.get('sovits_path')
    gpt_path = data.get('gpt_path')

    if not role or not sovits_path or not gpt_path:
        return {"success": False, "error": "缺少必要绑定参数"}

    char_cfg_file = ROOT_DIR / "characters" / role / "character.json"
    if not char_cfg_file.exists():
        return {"success": False, "error": f"角色 {role} 不存在"}

    with open(char_cfg_file, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    if "gpt_sovits" not in cfg:
        cfg["gpt_sovits"] = {}

    cfg["gpt_sovits"]["sovits_path"] = sovits_path.replace("\\", "/")
    cfg["gpt_sovits"]["gpt_path"] = gpt_path.replace("\\", "/")

    with open(char_cfg_file, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    return {"success": True, "message": f"成功为 [{role}] 绑定专属 5080 深度模型！"}

def main():
    import argparse
    parser = argparse.ArgumentParser(description="AI 娘声音工作台一体化服务")
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址")
    parser.add_argument("--port", type=int, default=5005, help="监听端口 (默认 5005)")
    args = parser.parse_args()

    print(f"=====================================================")
    print(f"  AI 娘声音工作台 (Voice Studio) 一体化服务启动！")
    print(f"  集成入口: http://{args.host}:{args.port}")
    print(f"  功能集成: 语音合成 + 5080 训练引擎 + 数据集管理")
    print(f"=====================================================")
    run(app, host=args.host, port=args.port, quiet=False)

if __name__ == '__main__':
    main()
