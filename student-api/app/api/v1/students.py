from uuid import UUID

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Response
from fastapi import status

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.logger import logger
from app.models.student import Student
from app.schemas.student import (
    StudentCreate,
    StudentResponse,
    StudentUpdate,
)

router = APIRouter(
    prefix="/api/v1/students",
    tags=["Students"],
)


@router.post(
    "",
    response_model=StudentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_student(
    student: StudentCreate,
    db: Session = Depends(get_db),
):
    """
    Create a new student.
    """

    db_student = Student(
        first_name=student.first_name,
        last_name=student.last_name,
        email=student.email,
        age=student.age,
    )

    try:
        db.add(db_student)
        db.commit()
        db.refresh(db_student)

    except IntegrityError:
        db.rollback()

        logger.warning(
            "duplicate_email",
            email=student.email,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Student with this email already exists.",
        )

    logger.info(
        "student_created",
        student_id=str(db_student.id),
        email=db_student.email,
    )

    return db_student


@router.get(
    "",
    response_model=list[StudentResponse],
)
def get_students(
    db: Session = Depends(get_db),
):
    """
    Get all students.
    """

    students = db.query(Student).all()

    logger.info(
        "students_fetched",
        count=len(students),
    )

    return students


@router.get(
    "/{student_id}",
    response_model=StudentResponse,
)
def get_student(
    student_id: UUID,
    db: Session = Depends(get_db),
):
    """
    Get student by ID.
    """

    student = db.get(Student, student_id)

    if student is None:

        logger.warning(
            "student_not_found",
            student_id=str(student_id),
        )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student not found.",
        )

    logger.info(
        "student_fetched",
        student_id=str(student.id),
    )

    return student


@router.put(
    "/{student_id}",
    response_model=StudentResponse,
)
def update_student(
    student_id: UUID,
    payload: StudentUpdate,
    db: Session = Depends(get_db),
):
    """
    Update an existing student.
    """

    student = db.get(Student, student_id)

    if student is None:

        logger.warning(
            "student_not_found",
            student_id=str(student_id),
        )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student not found.",
        )

    update_data = payload.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(student, field, value)

    try:
        db.commit()
        db.refresh(student)

    except IntegrityError:

        db.rollback()

        logger.warning(
            "duplicate_email",
            student_id=str(student_id),
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Student with this email already exists.",
        )

    logger.info(
        "student_updated",
        student_id=str(student.id),
    )

    return student


@router.delete(
    "/{student_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_student(
    student_id: UUID,
    db: Session = Depends(get_db),
):
    """
    Delete a student.
    """

    student = db.get(Student, student_id)

    if student is None:

        logger.warning(
            "student_not_found",
            student_id=str(student_id),
        )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student not found.",
        )

    db.delete(student)

    db.commit()

    logger.info(
        "student_deleted",
        student_id=str(student_id),
    )

    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
    )