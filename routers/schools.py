from fastapi import APIRouter, status

router = APIRouter(
    prefix="/schools",
    tags=["Schools"],
)


@router.get("/", status_code=status.HTTP_200_OK)
def get_schools():
    return {
        "status": "success",
        "message": "Schools list API is ready",
        "data": [],
    }


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_school():
    return {
        "status": "success",
        "message": "School created successfully",
        "data": {},
    }
