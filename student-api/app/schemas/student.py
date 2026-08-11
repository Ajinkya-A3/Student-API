from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import EmailStr
from pydantic import Field


class StudentCreate(BaseModel):
    first_name: str = Field(
        min_length=2,
        max_length=100,
    )

    last_name: str = Field(
        min_length=2,
        max_length=100,
    )

    email: EmailStr

    age: int = Field(
        ge=1,
        le=120,
    )


class StudentUpdate(BaseModel):
    first_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
    )

    last_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
    )

    email: EmailStr | None = None

    age: int | None = Field(
        default=None,
        ge=1,
        le=120,
    )


class StudentResponse(BaseModel):
    id: UUID

    first_name: str

    last_name: str

    email: EmailStr

    age: int

    model_config = ConfigDict(
        from_attributes=True,
    )
