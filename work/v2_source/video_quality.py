"""Restrained image cleanup and source-aspect export sizing."""
from __future__ import annotations

DEFAULT_ENHANCEMENT='Finom képjavítás'
ENHANCEMENTS={
    DEFAULT_ENHANCEMENT: 'Kíméletes végső élesítés és részletesebb MP4-mentés. A forrás arcát alapból változatlanul használja. Az AI-arcjavítás külön kapcsolható be; a generált arc mozgás közben változhat.',
    'Eredeti megjelenés': 'Utólagos képjavító szűrés nélkül. A forrás arcát alapból változatlanul használja; az opcionális AI-arcjavítás külön beállítás. A generált arc és a környezet részletei változhatnak.',
}
SOURCE_FORMAT='Fotó eredeti aránya'

def validate_enhancement(value):
    if value not in ENHANCEMENTS: raise ValueError('Válassz érvényes képjavítási módot.')
    return value

def source_size(image):
    from PIL import Image,ImageOps
    with Image.open(image) as source:
        width,height=ImageOps.exif_transpose(source).size
    ratio=min(1.0,1920/max(width,height))
    return max(2,round(width*ratio)//2*2),max(2,round(height*ratio)//2*2)

def pre_filter(value,already_clean=False):
    validate_enhancement(value)
    return 'hqdn3d=1.1:1.1:3:3' if value==DEFAULT_ENHANCEMENT and not already_clean else ''

def post_filter(value):
    validate_enhancement(value)
    return 'unsharp=5:5:0.35:3:3:0' if value==DEFAULT_ENHANCEMENT else ''
