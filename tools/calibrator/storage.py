"""Durable journal plus rollback-aware, atomic calibration config updates."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
from core import BODY_IDS, CalibrationError, word

def atomic(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if data is None:
        path.unlink(missing_ok=True)
    else:
        fd,name=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as f:
                f.write(data);f.flush();os.fsync(f.fileno())
            os.chmod(name,path.stat().st_mode & 0o777 if path.exists() else 0o644)
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

def encoded(value):return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()

class Journal:
    def __init__(self,directory):
        self.path=Path(directory);self.path.mkdir(parents=True,exist_ok=False,mode=0o700)
    def save(self,event,data):
        with (self.path/'journal.jsonl').open('a') as f:
            f.write(json.dumps(dict(event=event,time=time.time(),data=data),ensure_ascii=False)+'\n')
            f.flush();os.fsync(f.fileno())
        if event=='initial':atomic(self.path/'registers-before.json',encoded(data))

class Configs:
    def __init__(self,directory,journal,*,eeprom_only=False):
        self.eeprom_only=eeprom_only;self._mouth=None
        self.journal=journal
        if eeprom_only:
            self.paths={};self.expected={}
            journal.save('config-before',dict(mode='eeprom-only',configuration_files_accessed=False))
            return
        self.paths={k:Path(directory)/name for k,name in [('body','hd1910-body-calibration.json'),('mouth','hd1910-mouth.json')]}
        self.expected={k:p.read_bytes() if p.exists() else None for k,p in self.paths.items()}
        self.journal=journal
        journal.save('config-before',{k:None if v is None else v.decode() for k,v in self.expected.items()})
    @property
    def mouth(self):
        if self.eeprom_only:return copy.deepcopy(self._mouth)
        return json.loads(self.expected['mouth']) if self.expected['mouth'] else None
    def validate(self,rows):
        if self.eeprom_only:return # Session validates the complete hardware snapshot.
        if self.expected['body']:
            b=json.loads(self.expected['body'])
            offsets={x['id']:x['offset_raw'] for x in b['servos']}
            if b.get('schema')!=1 or len(b['servos'])!=14 or set(offsets)!=set(BODY_IDS) or any(offsets[s]!=word(rows[str(s)],31) for s in BODY_IDS):
                raise CalibrationError('现有身体标定文件与 EEPROM 不一致')
        m=self.mouth
        if not m or m.get('schema')!=1 or m.get('id')!=34 or m.get('mode')!=4 or m.get('eeprom_offset_raw')!=word(rows['34'],31) or m.get('closed_raw')!=2048 or not 1024<=m.get('open_raw',0)<=2032:
            raise CalibrationError('本机嘴部配置缺失或与 EEPROM 不一致')
    def commit(self,rows,mouth):
        if self.eeprom_only:
            self.journal.save('eeprom-only-saved',dict(offsets={sid:word(r,31) for sid,r in rows.items()},mouth=mouth))
            self._mouth=copy.deepcopy(mouth)
            return
        if any((p.read_bytes() if p.exists() else None)!=self.expected[k] for k,p in self.paths.items()):
            raise CalibrationError('标定配置文件被其他程序改变')
        new=copy.deepcopy(self.expected)
        # Old baseline has no body config: do not introduce a new daemon setting.
        if new['body']:
            b=json.loads(new['body'])
            for row in b['servos']:row['offset_raw']=word(rows[str(row['id'])],31)
            new['body']=encoded(b)
        if mouth!=self.mouth:new['mouth']=encoded(mouth)
        changed=[k for k in new if new[k]!=self.expected[k]]
        self.journal.save('config-intent',{k:None if v is None else v.decode() for k,v in new.items()})
        try:
            for k in changed:atomic(self.paths[k],new[k])
        except Exception as exc:
            failures=[]
            for k in changed:
                try:atomic(self.paths[k],self.expected[k])
                except Exception as rollback:failures.append(str(rollback))
            if failures:raise CalibrationError(f'文件保存失败且文件回滚未完成：{exc}; {failures}') from exc
            raise
        self.expected=new
