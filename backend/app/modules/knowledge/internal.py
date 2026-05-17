from fastapi import APIRouter

from app.modules.knowledge import schemas, service

router = APIRouter()


@router.post("/search", response_model=schemas.SearchResponse)
def search_knowledge(req: schemas.SearchRequest):
    results = service.search(
        hospital_id=req.hospital_id,
        query=req.query,
        top_k=req.top_k,
        category_ids=req.category_ids,
    )
    return schemas.SearchResponse(results=results)
