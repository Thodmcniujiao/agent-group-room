"""Global desktop display preferences, stored independently of credentials."""
import json
from pathlib import Path
import threading


class DisplaySettings:
    def __init__(self,path):self.path=Path(path);self.lock=threading.RLock()
    def snapshot(self):
        try:
            value=json.loads(self.path.read_text(encoding='utf-8'));size=value.get('chat_avatar_size')
            if type(size) is int and 24<=size<=96:return {'chat_avatar_size':size}
        except (OSError,ValueError,AttributeError):pass
        return {'chat_avatar_size':32}
    def save(self,value):
        if not isinstance(value,dict) or set(value)!={'chat_avatar_size'} or type(value['chat_avatar_size']) is not int or not 24<=value['chat_avatar_size']<=96:
            raise ValueError('聊天头像大小应为 24 至 96 像素的整数。')
        with self.lock:
            self.path.parent.mkdir(parents=True,exist_ok=True);pending=self.path.with_suffix('.new')
            pending.write_text(json.dumps(value),encoding='utf-8');pending.replace(self.path)
        return self.snapshot()
