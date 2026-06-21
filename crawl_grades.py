"""Fudan grade monitor — entry point.

Fetches grades, GPA, and ranking from fdjwgl (over WebVPN by default, or
directly when USE_DIRECT=1), diffs against the last encrypted snapshot,
and emails + commits only when something actually changed.

Why "only on change": GitHub auto-disables a scheduled workflow after a
stretch of repo inactivity.  By skipping the commit when nothing moved,
the repo stays quiet once a grade-release season ends, and the hourly
cron winds itself down — saving public Actions minutes without manual
intervention.
"""

import os
import sys

from src import config
from src.direct_session import DirectSession
from src.emailer import send_email
from src.encrypt import load_grades, save_grades
from src.grade_api import GradeClient
from src.webvpn import WebVPNSession


def login_with_retry(max_attempts: int = 3):
    """Build a session (WebVPN or direct) and authenticate, retrying on
    transient gateway hiccups."""
    use_vpn = config.USE_WEBVPN
    label = "WebVPN" if use_vpn else "direct"
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"\n[Login] {label} (attempt {attempt}/{max_attempts})...")
            session = WebVPNSession() if use_vpn else DirectSession()
            session.login()
            if use_vpn:
                # fdjwgl needs its own SSO on top of the VPN gateway session.
                session.authenticate_grade()
            return session
        except Exception as e:
            if attempt < max_attempts:
                print(f"  Failed: {type(e).__name__}: {e}; retrying...")
            else:
                raise


# ── snapshot building + diffing ──────────────────────────────────────────


def build_snapshot(client: GradeClient) -> dict:
    """Pull every grade signal into one comparable structure."""
    grade_sheet_id = client.discover_grade_sheet_id()
    print(f"[*] grade sheet id: {grade_sheet_id}")

    grades = client.get_all_grades(grade_sheet_id)
    statistic = client.get_grade_statistic(grade_sheet_id)

    dept = client.get_ranking(grade_sheet_id, scope="department")
    # Major ranking is derived from the same department cohort (filtered by
    # your major), so it needs no extra config.
    major = client.get_ranking(grade_sheet_id, scope="major")

    return {
        "grade_sheet_id": grade_sheet_id,
        "statistic": statistic,
        "grades": grades,
        "ranking": {
            "department": _rank_summary(dept),
            "major": _rank_summary(major),
        },
    }


def _rank_summary(rank: dict) -> dict:
    return {
        "total": rank["total"],
        "my_rank": rank["my_rank"],
        "my_gpa": rank["my_gpa"],
        "my_credits": rank["my_credits"],
    }


def _course_signature(grades: dict) -> dict:
    """Flatten grades into {courseCode: (name, gaGrade, gp, credits)}."""
    sig = {}
    for sem_grades in grades.get("semesterId2studentGrades", {}).values():
        for entry in sem_grades:
            code = entry.get("courseCode")
            if not code:
                continue
            sig[code] = (
                entry.get("courseName"),
                entry.get("gaGrade"),
                entry.get("gp"),
                entry.get("credits"),
            )
    return sig


def comparable(snapshot: dict) -> dict:
    """Extract the fields worth diffing (stable across snapshot versions)."""
    stat = (snapshot or {}).get("statistic", {}).get("totalStatistic", {})
    ranking = (snapshot or {}).get("ranking", {})
    return {
        "courses": _course_signature((snapshot or {}).get("grades", {})),
        "gpa": stat.get("gpa"),
        "credits": stat.get("credits"),
        "dept_rank": (ranking.get("department") or {}).get("my_rank"),
        "major_rank": (ranking.get("major") or {}).get("my_rank"),
    }


def diff_courses(old_sig: dict, new_sig: dict) -> list[dict]:
    """Return a list of per-course changes (new / updated)."""
    changes = []
    for code, (name, grade, gp, _credits) in new_sig.items():
        if code not in old_sig:
            changes.append(
                {"type": "new", "course": name, "grade": grade, "gp": gp}
            )
        elif old_sig[code][1:] != (name, grade, gp, _credits)[1:]:
            # grade or gp changed (name/credits ignored for diff)
            old_grade, old_gp = old_sig[code][1], old_sig[code][2]
            changes.append(
                {
                    "type": "updated",
                    "course": name,
                    "old_grade": old_grade,
                    "new_grade": grade,
                    "old_gp": old_gp,
                    "new_gp": gp,
                }
            )
    return changes


# ── notification ─────────────────────────────────────────────────────────


def render_email(new_snap: dict, old_snap: dict, course_changes: list[dict]) -> str:
    stat = new_snap["statistic"]["totalStatistic"]
    old_stat = (old_snap or {}).get("statistic", {}).get("totalStatistic", {})
    ranking = new_snap["ranking"]

    lines = ["亲爱的同学：\n", "您的复旦大学成绩有更新：\n"]
    for ch in course_changes:
        if ch["type"] == "new":
            gp = ch["gp"] if ch["gp"] is not None else "-"
            lines.append(f"  · [新出分] {ch['course']}：等级 {ch['grade']}，绩点 {gp}")
        else:
            lines.append(
                f"  · [更新] {ch['course']}："
                f"{ch['old_grade']}/{ch['old_gp']} → {ch['new_grade']}/{ch['new_gp']}"
            )

    lines.append("")
    lines.append(f"累计 GPA：{stat.get('gpa')}（学分 {stat.get('credits')}，{stat.get('count')} 门）")
    old_gpa = old_stat.get("gpa")
    if old_gpa and old_gpa != stat.get("gpa"):
        arrow = "↑" if float(stat.get("gpa", 0)) > float(old_gpa) else "↓"
        lines.append(f"GPA 变化：{old_gpa} {arrow} {stat.get('gpa')}")

    dept = ranking.get("department") or {}
    if dept.get("my_rank") is not None:
        lines.append(f"院系排名：{dept['my_rank']}/{dept['total']}")
    major = ranking.get("major") or {}
    if major.get("my_rank") is not None:
        lines.append(f"专业排名：{major['my_rank']}/{major['total']}")

    return "\n".join(lines)


def email_subject(course_changes: list[dict], old_gpa, new_gpa) -> str:
    prefix = "【自动推送】"
    try:
        if new_gpa and old_gpa and float(new_gpa) > float(old_gpa):
            prefix += "好消息！"
        elif new_gpa and old_gpa and float(new_gpa) < float(old_gpa):
            prefix += "坏消息！"
        elif course_changes:
            prefix += "成绩更新！"
    except (TypeError, ValueError):
        prefix += "成绩更新！"
    names = "/".join(c["course"] for c in course_changes[:3])
    return f"{prefix}{names or '成绩'}出分了"


# ── main ─────────────────────────────────────────────────────────────────


def main():
    if not config.STUDENT_ID or not config.PASSWORD:
        print("[-] Error: StuId and UISPsw environment variables must be set.")
        sys.exit(1)

    session = login_with_retry()
    client = GradeClient(session)

    new_snap = build_snapshot(client)
    old_snap = load_grades()

    old_comp = comparable(old_snap)
    new_comp = comparable(new_snap)

    course_changes = diff_courses(old_comp["courses"], new_comp["courses"])

    is_first_run = not old_snap
    # Trigger only on the student's own grade events: a course newly graded
    # or its grade/gp changed ("课程出分"). GPA/credits are downstream of
    # those and never move on their own. Ranking is deliberately NOT a
    # trigger — someone else getting graded can shift your rank without any
    # of your own grades changing, and we don't treat that as an event: no
    # email, no snapshot rewrite, so the stored history stays aligned with
    # your own grade timeline rather than jittering with peers.
    grade_event = bool(course_changes)

    if not grade_event and not is_first_run:
        if old_comp["dept_rank"] != new_comp["dept_rank"] or old_comp["major_rank"] != new_comp["major_rank"]:
            print("[*] Only ranking moved (peers graded) — not an event; "
                  "snapshot left untouched.")
        else:
            print("[*] No changes since last run — snapshot left untouched.")
        return

    # Build + send notification.
    stat = new_snap["statistic"]["totalStatistic"]
    if is_first_run:
        # First run: don't spam every historical course; just confirm init.
        body = (
            "成绩监控已初始化。\n\n"
            f"累计 GPA：{stat.get('gpa')}（学分 {stat.get('credits')}，{stat.get('count')} 门）\n"
        )
        dept = new_snap["ranking"].get("department") or {}
        if dept.get("my_rank") is not None:
            body += f"院系排名：{dept['my_rank']}/{dept['total']}\n"
        major = new_snap["ranking"].get("major") or {}
        if major.get("my_rank") is not None:
            body += f"专业排名：{major['my_rank']}/{major['total']}\n"
        send_email("【自动推送】成绩监控已启动", body)
    else:
        body = render_email(new_snap, old_snap, course_changes)
        subject = email_subject(
            course_changes, old_comp["gpa"], new_comp["gpa"]
        )
        send_email(subject, body)

    # Persist only on a real grade event (or init), so the workflow can
    # skip the commit and let the repo go idle after grade season.
    save_grades(new_snap)


if __name__ == "__main__":
    main()
