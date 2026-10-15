import pydantic


class Model(pydantic.BaseModel):
    name: str

    class Config:
        orm_mode = True
