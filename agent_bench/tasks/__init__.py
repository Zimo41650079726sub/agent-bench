from .base import BenchTask, Skill, BenchContext
from .debug_python import TaskDebugPython
from .tdd import TaskTDD
from .context_manage import TaskContextManage
from .skill_driven import TaskSkillDriven
from .big_file_edit import TaskBigFileEdit
from .doc_trap import TaskDocTrap
from .wrong_fix import TaskWrongFixTrap
from .tdd_strict import TaskTDDStrict
from .long_procedure import TaskLongProcedure

BUILTIN_TASKS = {
    TaskDebugPython.id: TaskDebugPython,
    TaskTDD.id: TaskTDD,
    TaskContextManage.id: TaskContextManage,
    TaskDocTrap.id: TaskDocTrap,
    TaskWrongFixTrap.id: TaskWrongFixTrap,
    TaskTDDStrict.id: TaskTDDStrict,
    TaskBigFileEdit.id: TaskBigFileEdit,
    TaskLongProcedure.id: TaskLongProcedure,
    TaskSkillDriven.id: TaskSkillDriven,
}
