#!/usr/bin/env python3
"""Build a new test IMG offline. Never writes a block device or alters the source IMG."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import stat
import struct
import subprocess

# Set by build(); this module is an offline regular-file image writer only.
OUT=BASE=PAYLOAD=WORK=FS=IMAGE=LOG=BOOT0=None
EXPECTED='d4bb7c8bd1bcf06908107ac803f6d6abdbf28d23d7f36e542e4c8e47077c0bcb'
BOOT0_SHA='5ec7f17be1f2f653496529f0b2cd19a18977d4d122d458969a7cd855ab6d35fb'
BOOT0_OFFSET=8192


def build(base, payload, boot0, output, prepared_fs=None):
    global OUT,BASE,PAYLOAD,WORK,FS,IMAGE,LOG,BOOT0
    OUT=output;BASE=base;PAYLOAD=payload;BOOT0=boot0
    WORK=OUT/'work';WORK.mkdir(exist_ok=True)
    LOG=OUT/'validation';LOG.mkdir(exist_ok=True)
    FS=WORK/'rootfs.ext4'
    IMAGE=OUT/'release/Microduck_OrangePiZero3W_Experimental.img'
    main(prepared_fs)


def tool(name):
    path=shutil.which(name)
    if path is None:raise RuntimeError('Missing tool on PATH: '+name)
    return path


def digest(path,offset=0,length=None):
    h=hashlib.sha256()
    with path.open('rb') as f:
        f.seek(offset)
        while length is None or length:
            block=f.read(4*1024*1024 if length is None else min(length,4*1024*1024))
            if not block:break
            h.update(block)
            if length is not None:length-=len(block)
    return h.hexdigest()


def command(args,allowed=(0,)):
    p=subprocess.run(list(map(str,args)),capture_output=True,text=True)
    if p.returncode not in allowed:raise RuntimeError(p.stdout+p.stderr)
    return p.stdout+p.stderr


def token(path):
    value=str(path)
    if any(c in value for c in '\n\r"\\'):raise ValueError('Unsupported debugfs path')
    return '"'+value+'"'


def remote_stat(path):
    output=command([tool('debugfs'),'-R','stat '+token(path),FS])
    return output if 'Inode:' in output else None


def main(prepared_fs):
    if not BASE.is_file() or BASE.is_block_device():raise ValueError('Expected original regular IMG file')
    if IMAGE.exists():raise ValueError('Output IMG already exists; preserve or move it before rebuilding')
    assert digest(BASE)==EXPECTED,'Source image hash mismatch'
    blob=BOOT0.read_bytes()
    assert len(blob)==258048 and digest(BOOT0)==BOOT0_SHA
    assert blob[4:12]==b'eGON.BT0' and struct.unpack_from('<I',blob,16)[0]==len(blob)
    words=list(struct.unpack('<'+'I'*(len(blob)//4),blob))
    checksum=words[3];words[3]=0x5f0a6c39
    assert sum(words)&0xffffffff==checksum,'boot0 checksum mismatch'
    with BASE.open('rb') as source:
        source.seek(BOOT0_OFFSET)
        old=source.read(len(blob));old_len=struct.unpack_from('<I',old,16)[0]
        assert old[4:12]==b'eGON.BT0' and old_len==245760
        assert old[old_len:]==bytes(len(blob)-old_len),'boot0 would overwrite nonempty padding'
    with BASE.open('rb') as src:
        header=bytearray(src.read(512));assert header[510:512]==b'\x55\xaa'
        start,sectors=struct.unpack_from('<II',header,454)
        offset=start*512
        assert offset==33554432 and header[450]==0x83
        src.seek(offset)
        if prepared_fs:
            prepared_fs.rename(FS)
        else:
            with FS.open('wb') as dest:shutil.copyfileobj(src,dest,4*1024*1024)
    logs=[command([tool('e2fsck'),'-f','-y',FS],allowed=(0,1)),command([tool('resize2fs'),FS,'4G'])]
    assert FS.stat().st_size==4294967296
    (LOG/'filesystem-resize.log').write_text('\n'.join(logs))
    entries=[p for p in sorted(PAYLOAD.rglob('*')) if '__pycache__' not in p.parts]
    commands=[];metadata={};removed=[]
    # The official base ships host keys; every new board must generate its own.
    for kind in ('rsa','ecdsa','ed25519','dsa'):
        for suffix in ('','.pub'):
            path='/etc/ssh/ssh_host_'+kind+'_key'+suffix
            if remote_stat(path):commands.append('rm '+token(path));removed.append(path)
    for p in sorted((p for p in entries if p.is_dir() and not p.is_symlink()),key=lambda p:len(p.parts)):
        target='/'+str(p.relative_to(PAYLOAD))
        existing=remote_stat(target)
        if existing and 'Type: directory' not in existing:raise ValueError('Unexpected non-directory '+target)
        if not existing:commands.append('mkdir '+token(target))
        commands += ['set_inode_field '+token(target)+' uid 0','set_inode_field '+token(target)+' gid 0',
                     'set_inode_field '+token(target)+' mode '+('0'+format(stat.S_IFDIR|stat.S_IMODE(p.stat().st_mode),'o'))]
    for p in entries:
        if p.is_dir() and not p.is_symlink():continue
        target='/'+str(p.relative_to(PAYLOAD));existing=remote_stat(target)
        if existing:
            if 'Type: directory' in existing:raise ValueError('Refusing to overwrite directory '+target)
            commands.append('rm '+token(target))
        if p.is_symlink():
            commands.append('symlink '+token(target)+' '+token(p.readlink()))
            metadata[target]={'symlink':str(p.readlink())}
        else:
            commands += ['write '+token(p)+' '+token(target),
                         'set_inode_field '+token(target)+' mode '+('0'+format(stat.S_IFREG|stat.S_IMODE(p.stat().st_mode),'o'))]
            metadata[target]={'sha256':digest(p),'bytes':p.stat().st_size,'mode':oct(stat.S_IMODE(p.stat().st_mode))}
        commands += ['set_inode_field '+token(target)+' uid 0','set_inode_field '+token(target)+' gid 0']
    script=WORK/'inject.debugfs';script.write_text('\n'.join(commands)+'\n')
    output=command([tool('debugfs'),'-w','-f',script,FS])
    (LOG/'image-injection.log').write_text(output)
    if any(error in output for error in ("Couldn't", 'File not found', 'Command not found', 'Invalid argument')):
        raise RuntimeError('debugfs injection reported an error; see image-injection.log')
    # debugfs may return exit 0 on a command error: verify every installed byte.
    verified=WORK/'roundtrip';verified.mkdir(exist_ok=True)
    dump=[]
    for target,info in metadata.items():
        if 'sha256' in info:
            dest=verified/target.lstrip('/');dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists():dest.unlink()
            dump.append('dump '+token(target)+' '+token(dest))
    script=WORK/'roundtrip.debugfs';script.write_text('\n'.join(dump)+'\n')
    (LOG/'roundtrip.log').write_text(command([tool('debugfs'),'-f',script,FS]))
    for target,info in metadata.items():
        observed=remote_stat(target)
        if not observed or 'User:     0' not in observed or 'Group:     0' not in observed:
            raise RuntimeError('Missing file or wrong root ownership: '+target)
        if 'sha256' in info:
            assert digest(verified/target.lstrip('/'))==info['sha256'],target
            # debugfs presents permissions without the file-type bits.
            expected=f'{int(info["mode"],8):04o}'
            assert 'Mode:  '+expected in observed,target+' mode mismatch: '+observed
        else:
            assert 'Fast link dest: "'+info['symlink']+'"' in observed,target
    for target in removed:assert remote_stat(target) is None,target+' was not removed'
    (LOG/'filesystem-check.log').write_text(command([tool('e2fsck'),'-f','-n',FS]))
    struct.pack_into('<I',header,458,FS.stat().st_size//512)
    IMAGE.parent.mkdir(exist_ok=True)
    with IMAGE.open('xb') as dst,BASE.open('rb') as src:
        os.chmod(IMAGE,0o600);dst.write(header);src.seek(512);remaining=offset-512
        while remaining:
            block=src.read(min(remaining,4*1024*1024));assert block;dst.write(block);remaining-=len(block)
        with FS.open('rb') as filesystem:shutil.copyfileobj(filesystem,dst,4*1024*1024)
        dst.seek(BOOT0_OFFSET);dst.write(blob);dst.flush();os.fsync(dst.fileno())
    assert IMAGE.stat().st_size==offset+FS.stat().st_size
    assert digest(IMAGE,BOOT0_OFFSET,len(blob))==BOOT0_SHA
    assert digest(IMAGE,512,BOOT0_OFFSET-512)==digest(BASE,512,BOOT0_OFFSET-512)
    end=BOOT0_OFFSET+len(blob)
    assert digest(IMAGE,end,offset-end)==digest(BASE,end,offset-end),'Boot bytes outside boot0 changed'
    assert digest(IMAGE,offset)==digest(FS),'Filesystem copy mismatch'
    (LOG/'payload-manifest.json').write_text(json.dumps(metadata,indent=2)+'\n')
    record={'source_image_sha256':EXPECTED,'image':IMAGE.name,'image_bytes':IMAGE.stat().st_size,
            'image_sha256':digest(IMAGE),'root_partition_offset_bytes':offset,
            'root_partition_bytes':FS.stat().st_size,'boot_area_outside_mbr_and_boot0_preserved':True,
            'boot0_1gb_fix_sha256':BOOT0_SHA,'boot0_offset_bytes':BOOT0_OFFSET,
            'boot0_bytes':len(blob),'boot0_checksum_verified':True,
            'bootloader_rebuilt':False,'source_image_modified':False,'payload_roundtrip_files_verified':len(metadata),
            'filesystem_check':'passed','board_boot_test':'not_performed','wifi_connection_test':'not_performed',
            'component_hardware_tests':'see README; public firmware has not been booted',
            'camera_test':'IMX219 component tested on the board; new image unbooted' if prepared_fs else 'vendor module only',
            'inherited_ssh_host_keys_removed':removed,
            'sd_card_written':False,'contains_operator_provisioning':(PAYLOAD/'etc/netplan/30-microduck-wifi.yaml').exists()}
    (LOG/'image-build.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record,indent=2),flush=True)
