from .base import BenchTask, Skill, BenchContext
from .debug_python import TaskDebugPython
from .tdd import TaskTDD
from .context_manage import TaskContextManage
from .skill_driven import TaskSkillDriven

BUILTIN_TASKS = {
    TaskDebugPython.id: TaskDebugPython,
    TaskTDD.id: TaskTDD,
    TaskContextManage.id: TaskContextManage,
    TaskSkillDriven.id: TaskSkillDriven,
}
