"""Publish complete frame sequences while preserving an earlier successful run."""
from pathlib import Path
import uuid

def staging_folder(scene,label):
    path=Path(scene).resolve()/(label+'.pending_'+uuid.uuid4().hex)
    path.mkdir()
    return path

def publish_folder(staging,destination):
    staging,destination=Path(staging),Path(destination)
    if staging.parent.resolve()!=destination.parent.resolve() or not staging.is_dir():
        raise ValueError('A képkockamappák csak ugyanabban a jelenetben cserélhetők.')
    if any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) for p in (staging,destination)):
        raise ValueError('A képkockamappa nem lehet átirányított mappa.')
    backup=None
    if destination.exists():
        backup=destination.with_name(destination.name+'.previous_'+uuid.uuid4().hex)
        destination.replace(backup)
    try:staging.replace(destination)
    except BaseException:
        if backup is not None and not destination.exists():backup.replace(destination)
        raise
    return destination
