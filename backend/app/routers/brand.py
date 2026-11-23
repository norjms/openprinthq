"""The shop's own logo, /api/brand/v1 (docs/brand.md).

Anyone may read the wordmark: it is what the shop shows to everybody, and the phone app needs it before signing in.
Changing it changes it for everybody, so it needs the hub owner, or the settings permission in the installation's
first team, as for the crafts.
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from ..auth import current
from ..services import brand
from .crafts import may_change

router = APIRouter(prefix="/api/brand", tags=["brand"])
SAFE = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=300"}


@router.get("/v1")
def get_brand() -> dict:
    return {"wordmark": brand.url()}


@router.get("/v1/wordmark")
def wordmark() -> Response:
    found = brand.current()
    if not found:
        raise HTTPException(404, "this hub has no logo of its own")
    path, media = found
    return Response(path.read_bytes(), media_type=media, headers=SAFE)


@router.put("/v1/wordmark")
async def put_wordmark(file: UploadFile = File(...), p=Depends(current)) -> dict:
    if not may_change(p):
        raise HTTPException(403, "only the hub owner or an admin of the first team can change the logo")
    data = await file.read(brand.MAX_BYTES + 1)
    try:
        brand.save(data, (file.content_type or "").split(";")[0].strip().lower())
    except brand.BadLogo as e:
        raise HTTPException(422, str(e)) from e
    return {"wordmark": brand.url()}


@router.delete("/v1/wordmark")
def delete_wordmark(p=Depends(current)) -> dict:
    if not may_change(p):
        raise HTTPException(403, "only the hub owner or an admin of the first team can change the logo")
    brand.remove()
    return {"wordmark": None}
