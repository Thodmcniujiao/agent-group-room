"""Local raster avatars, separate from credentials and room settings."""
import base64
import binascii
import json
import os
from pathlib import Path
import threading


class Avatars:
    def __init__(self,path):self.path=Path(path);self.lock=threading.RLock()
    def snapshot(self):
        try:
            value=json.loads(self.path.read_text(encoding='utf-8'))
            return value if isinstance(value,dict) else {}
        except (OSError,ValueError):return {}
    def save(self,identity,image):
        if image is not None:
            if not isinstance(image,str) or len(image)>90000:raise ValueError('头像需小于 64 KB，请重新选择图片。')
            prefix,sep,encoded=image.partition(',')
            kinds={'data:image/png;base64':'png','data:image/jpeg;base64':'jpeg','data:image/webp;base64':'webp'}
            if not sep or prefix not in kinds:raise ValueError('头像仅支持 PNG、JPEG、WebP 图片。')
            try:data=base64.b64decode(encoded,validate=True)
            except (binascii.Error,ValueError):raise ValueError('头像图片无效。') from None
            valid=(kinds[prefix]=='png' and data.startswith(b'\x89PNG\r\n\x1a\n') or
                kinds[prefix]=='jpeg' and data.startswith(b'\xff\xd8\xff') or
                kinds[prefix]=='webp' and data[:4]==b'RIFF' and data[8:12]==b'WEBP')
            if not valid or not 16<=len(data)<=65536:raise ValueError('头像图片无效或超过 64 KB。')
        with self.lock:
            value=self.snapshot()
            if image is None:value.pop(identity,None)
            else:value[identity]=image
            self.path.parent.mkdir(parents=True,exist_ok=True);pending=self.path.with_suffix('.new')
            pending.write_text(json.dumps(value),encoding='utf-8');os.replace(pending,self.path)
            return value

    def forget_prefix(self,prefix):
        with self.lock:
            value=self.snapshot();kept={k:v for k,v in value.items() if not k.startswith(prefix)}
            if len(kept)==len(value):return value
            self.path.parent.mkdir(parents=True,exist_ok=True);pending=self.path.with_suffix('.new')
            pending.write_text(json.dumps(kept),encoding='utf-8');os.replace(pending,self.path)
            return kept
