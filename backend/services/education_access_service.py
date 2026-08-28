"""教学资源所有权与成员资格判定。"""

from __future__ import annotations

from dataclasses import dataclass

from storage.education_repository import EducationRepository


@dataclass(frozen=True)
class EducationAccessError(PermissionError):
    message: str
    status: int
    code: str | None = None

    def __str__(self) -> str:
        return self.message


class EducationAccessService:
    """集中处理角色、移除状态和资源归属，避免各路由出现不同判定。"""

    def __init__(self, repository: EducationRepository) -> None:
        self._repository = repository

    def membership(
        self,
        public_class_id: str,
        *,
        user_id: int,
        selected_role: str,
        allowed_roles: set[str] | None = None,
        require_student_profile: bool = True,
    ) -> dict:
        membership = self._repository.get_membership(public_class_id, user_id)
        if membership is None:
            raise EducationAccessError("class not found", 404)
        if membership["role"] != selected_role:
            raise EducationAccessError(
                "class is not available in the selected education role",
                403,
                "education_role_forbidden",
            )
        if allowed_roles and membership["role"] not in allowed_roles:
            raise EducationAccessError("forbidden", 403)
        if (
            require_student_profile
            and membership["role"] == "student"
            and not (
                membership.get("student_name")
                and membership.get("student_number")
            )
        ):
            raise EducationAccessError(
                "student profile is incomplete",
                409,
                "student_profile_required",
            )
        return membership

    def class_teacher(self, public_class_id: str, *, user_id: int) -> dict:
        membership = self.membership(
            public_class_id,
            user_id=user_id,
            selected_role="teacher",
            allowed_roles={"teacher"},
        )
        if int(membership["owner_user_id"]) != int(user_id):
            raise EducationAccessError("forbidden", 403)
        return membership
