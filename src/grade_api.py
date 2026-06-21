"""Grade-system API client.

Wraps fdjwgl's grade endpoints behind one object that is transport-
agnostic: pass it a :class:`src.webvpn.WebVPNSession` for off-campus runs
or a :class:`src.direct_session.DirectSession` on the campus network —
both expose the same ``get``/``post`` surface.

Endpoints (all under fdjwgl.fudan.edu.cn):

* ``info/{id}``              — every semester's grades, credits included
* ``grade-statistic/{id}``   — GPA + credit + course counts (total + per-term)
* ``get-not-retake-grade/{id}`` — grade IDs that are not retakes
* ``my-gpa/search``          — ranking (department needs no extra config;
  major needs ``MAJOR_ASSOC``)
"""

import re

from src import config


class GradeClient:
    """Read-only client over fdjwgl's grade APIs."""

    def __init__(self, session):
        # session is a WebVPNSession or DirectSession (duck-typed: get/post).
        self.session = session
        self.base = config.GRADE_BASE

    # ── discovery ─────────────────────────────────────────────────────────

    def discover_grade_sheet_id(self) -> str:
        """Hit the grade-sheet landing URL and read the per-student id.

        fdjwgl redirects ``/grade/sheet/`` to ``/grade/sheet/semester-index/{id}``;
        the id is the same one the info/statistic endpoints key on.
        """
        resp = self.session.get(config.GRADE_HOME_URL, timeout=30)
        for source in (resp.url, resp.text or ""):
            match = re.search(r"semester-index/(\d+)", source)
            if match:
                return match.group(1)
        # Fall back to the alternate path shape some deployments use.
        match = re.search(r"grade/sheet/info/(\d+)", resp.text or "")
        if match:
            return match.group(1)
        raise RuntimeError(
            f"Could not detect grade sheet id (final URL: {resp.url[:120]})"
        )

    # ── grade data ────────────────────────────────────────────────────────

    def get_all_grades(self, grade_sheet_id: str) -> dict:
        """Return every semester's grades with credits and GP already set.

        Shape::

            {
              "semesterId2studentGrades": {"<semId>": [ {courseCode, courseName,
                gaGrade, gp, credits, ...}, ... ]},
              "semesters": [...],
              "id2semesters": {...},
            }
        """
        resp = self.session.get(
            f"{self.base}/student/for-std/grade/sheet/info/{grade_sheet_id}",
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_grade_statistic(self, grade_sheet_id: str) -> dict:
        """Return official GPA / credit / course counts.

        Shape::

            {
              "totalStatistic": {"gpa": "3.89", "credits": "83.5", "count": 33},
              "id2Statistic":   {"<semId>": {"gpa": ..., "credits": ..., "count": ...}},
            }

        No more manual GPA arithmetic — the server already excludes P/NP
        and retakes the way the registrar does.
        """
        resp = self.session.get(
            f"{self.base}/student/for-std/grade/sheet/grade-statistic/{grade_sheet_id}",
            params={"semester": ""},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_not_retake_ids(self, grade_sheet_id: str) -> list[int]:
        """Return the set of grade-row ids that are not retakes.

        Useful for filtering retake duplicates out of ``info`` if you ever
        need to compute something the statistic endpoint doesn't cover.
        """
        resp = self.session.get(
            f"{self.base}/student/for-std/grade/sheet/get-not-retake-grade/{grade_sheet_id}",
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("notRetakeGradeIds", [])

    def get_ranking(
        self, grade_sheet_id: str, scope: str = "department",
        major_assoc: str = None, student_code: str = None,
    ) -> dict:
        """Return class/department ranking.

        ``scope='department'`` (default) needs no extra config — the server
        infers the student's department and grade from ``grade_sheet_id``.
        ``scope='major'`` narrows to the student's major and needs the
        internal ``majorAssoc`` id (set via ``MAJOR_ASSOC`` env or the
        ``major_assoc`` arg).

        Returns::

            {
              "scope": "department"|"major",
              "total": <int>,        # students in scope
              "my_rank": <int>|None, # this student's rank (ties share a rank)
              "my_gpa": <float>|None,
              "my_credits": <float>|None,
              "rows": [ {code, name, major, department, gpa, credit, ranking}, ... ],
            }
        """
        if scope not in ("department", "major"):
            raise ValueError(f"unknown scope: {scope!r}")

        params: dict[str, str] = {"studentAssoc": grade_sheet_id}
        if scope == "major":
            major_assoc = major_assoc or config.MAJOR_ASSOC
            if not major_assoc:
                raise ValueError(
                    "Major ranking needs MAJOR_ASSOC (env var) or major_assoc arg."
                )
            params["majorAssoc"] = major_assoc

        resp = self.session.get(
            f"{self.base}/student/for-std/grade/my-gpa/search",
            params=params,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        rows = data.get("data", []) or []
        total = int(data.get("_page_", {}).get("totalRows", len(rows)))

        # The student's own row is the only one with a non-masked code/name.
        student_code = student_code or config.STUDENT_ID
        mine = next((r for r in rows if r.get("code") == student_code), None)

        return {
            "scope": scope,
            "total": total,
            "my_rank": mine.get("ranking") if mine else None,
            "my_gpa": mine.get("gpa") if mine else None,
            "my_credits": mine.get("credit") if mine else None,
            "rows": rows,
        }
