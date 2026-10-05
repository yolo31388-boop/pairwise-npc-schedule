"""
游戏内NPC日程规划与时间管理系统 - 核心模块

五大子系统：
- plan_schedule:  日程规划（时间轴 + 优先级）
- time_manager:   时间管理（昼夜周期 + 季节变化）
- routine:        作息安排（需求 + 恢复）
- task_scheduler: 任务调度（依赖 + 冲突）
- state_machine:  状态机（转换条件 + 动作）

update(dt) 中的 dt 以“游戏小时”为单位（可用 config["time_scale"] 换算）。
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
import math
import time


SEASONS = ("spring", "summer", "autumn", "winter")


@dataclass
class Config:
    pass


@dataclass
class Task:
    """一个可被规划 / 调度的 NPC 任务。"""
    id: str
    name: str = ""
    location: str = "plaza"
    priority: int = 0                # 数值越大越重要
    duration: float = 1.0            # 时长（游戏小时）
    earliest_start: float = 0.0      # 时间轴：最早开始时刻（时）
    latest_end: float = 24.0         # 时间轴：最晚结束时刻（时）
    dependencies: List[str] = field(default_factory=list)  # 前置任务 id
    status: str = "pending"          # pending / running / done
    progress: float = 0.0

    def __post_init__(self):
        if not self.name:
            self.name = self.id


def _coerce_task(task: Any) -> Task:
    if isinstance(task, Task):
        return task
    if isinstance(task, dict):
        keys = ("id", "name", "location", "priority", "duration",
                "earliest_start", "latest_end", "dependencies")
        return Task(**{k: v for k, v in task.items() if k in keys})
    return Task(id=str(task))


class TimeManager:
    """时间管理：昼夜周期 + 季节变化。"""

    def __init__(self, config: Optional[Dict] = None):
        config = config or {}
        self.day_length = float(config.get("day_length", 24.0))
        self.days_per_season = int(config.get("days_per_season", 30))
        self.day_start = float(config.get("day_start", 6.0))
        self.night_start = float(config.get("night_start", 18.0))
        self.seasonal_daylight = bool(config.get("seasonal_daylight", True))
        self.time_of_day = float(config.get("start_time", 8.0))
        self.day = int(config.get("start_day", 1))

    def advance(self, dt_hours: float):
        self.time_of_day += dt_hours
        while self.time_of_day >= self.day_length:
            self.time_of_day -= self.day_length
            self.day += 1

    @property
    def season(self) -> str:
        """季节变化：每 days_per_season 天轮换一个季节。"""
        idx = ((self.day - 1) // max(1, self.days_per_season)) % len(SEASONS)
        return SEASONS[idx]

    def daylight_window(self) -> Tuple[float, float]:
        """不同季节昼夜长短不同：夏季白天长，冬季白天短。"""
        adjust = 0.0
        if self.seasonal_daylight:
            adjust = {"spring": 0.0, "summer": 2.0,
                      "autumn": 0.0, "winter": -2.0}[self.season]
        return self.day_start - adjust, self.night_start + adjust

    def is_day(self) -> bool:
        start, end = self.daylight_window()
        return start <= self.time_of_day < end

    def is_night(self) -> bool:
        return not self.is_day()

    def __call__(self) -> Dict:
        return {
            "time_of_day": self.time_of_day,
            "day": self.day,
            "season": self.season,
            "is_day": self.is_day(),
            "is_night": self.is_night(),
        }


class RoutineManager:
    """作息安排：需求（饥饿 / 疲劳）+ 恢复（吃饭 / 睡觉）。"""

    def __init__(self, config: Optional[Dict] = None):
        config = config or {}
        self.hunger = float(config.get("initial_hunger", 0.0))    # 0 饱 .. 100 饿
        self.fatigue = float(config.get("initial_fatigue", 0.0))  # 0 精神 .. 100 困
        self.hunger_rate = float(config.get("hunger_rate", 4.0))
        self.fatigue_rate = float(config.get("fatigue_rate", 5.0))
        self.eat_recovery = float(config.get("eat_recovery", 60.0))
        self.sleep_recovery_rate = float(config.get("sleep_recovery_rate", 12.0))
        self.hunger_threshold = float(config.get("hunger_threshold", 60.0))
        self.fatigue_threshold = float(config.get("fatigue_threshold", 70.0))

    def update(self, dt_hours: float, sleeping: bool = False):
        self.hunger = min(100.0, self.hunger + self.hunger_rate * dt_hours)
        if sleeping:
            self.fatigue = max(0.0, self.fatigue - self.sleep_recovery_rate * dt_hours)
        else:
            self.fatigue = min(100.0, self.fatigue + self.fatigue_rate * dt_hours)

    @property
    def hungry(self) -> bool:
        return self.hunger >= self.hunger_threshold

    @property
    def tired(self) -> bool:
        return self.fatigue >= self.fatigue_threshold

    def eat(self):
        """吃饭后饥饿恢复。"""
        self.hunger = max(0.0, self.hunger - self.eat_recovery)

    def sleep(self, hours: float):
        """睡觉后疲劳恢复。"""
        self.fatigue = max(0.0, self.fatigue - self.sleep_recovery_rate * hours)

    def __call__(self) -> Dict:
        return {
            "hunger": self.hunger,
            "fatigue": self.fatigue,
            "hungry": self.hungry,
            "tired": self.tired,
        }


class TaskScheduler:
    """任务调度：依赖（前置完成后才能开始）+ 冲突（同一时间只做一件事）。"""

    def __init__(self):
        self.tasks: Dict[str, Task] = {}
        self.current_id: Optional[str] = None
        self.completed: List[str] = []

    def add_task(self, task: Any) -> Task:
        task = _coerce_task(task)
        self.tasks[task.id] = task
        return task

    def dependencies_met(self, task_id: str) -> bool:
        task = self.tasks[task_id]
        return all(dep in self.completed for dep in task.dependencies)

    def ready_tasks(self, now: float = 0.0) -> List[Task]:
        return [t for t in self.tasks.values()
                if t.status == "pending"
                and t.earliest_start <= now
                and self.dependencies_met(t.id)]

    def start_next(self, now: float = 0.0) -> Optional[Task]:
        # 冲突处理：同一时间只能做一件事
        if self.current_id is not None:
            return None
        ready = self.ready_tasks(now)
        if not ready:
            return None
        # 优先级高的先执行，同级按时间轴先后
        ready.sort(key=lambda t: (-t.priority, t.earliest_start, t.id))
        task = ready[0]
        task.status = "running"
        self.current_id = task.id
        return task

    @property
    def current(self) -> Optional[Task]:
        return self.tasks.get(self.current_id) if self.current_id else None

    def update(self, dt_hours: float) -> Optional[Task]:
        """推进当前任务，完成时返回该任务。"""
        task = self.current
        if task is None:
            return None
        task.progress += dt_hours
        if task.progress >= task.duration:
            task.status = "done"
            self.completed.append(task.id)
            self.current_id = None
            return task
        return None

    def __call__(self) -> Dict:
        return {
            "current": self.current_id,
            "completed": list(self.completed),
            "pending": [t.id for t in self.tasks.values() if t.status == "pending"],
        }


class StateMachine:
    """状态机：转换条件 + 每个状态的动作。"""

    def __init__(self, initial: str = "idle"):
        self.initial = initial
        self.state = initial
        self.transitions: List[Tuple[str, str, Callable[[Dict], bool]]] = []
        self.actions: Dict[str, Callable[[Dict], Any]] = {}
        self.history: List[str] = [initial]

    def add_transition(self, source: str, target: str,
                       condition: Callable[[Dict], bool]):
        self.transitions.append((source, target, condition))

    def set_action(self, state: str, action: Callable[[Dict], Any]):
        self.actions[state] = action

    def update(self, context: Optional[Dict] = None) -> Any:
        context = context or {}
        for source, target, condition in self.transitions:
            if source == self.state and condition(context):
                self.state = target
                self.history.append(target)
                break
        action = self.actions.get(self.state)
        if action is not None:
            return action(context)
        return None

    def reset(self):
        self.state = self.initial
        self.history = [self.initial]

    def __call__(self) -> str:
        return self.state


class NPCSchedule:
    """游戏内NPC日程规划与时间管理系统。"""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self._state: Dict = {}
        self._history: List[Dict] = []
        self.time_scale = float(self.config.get("time_scale", 1.0))
        self.time_manager = TimeManager(self.config.get("time"))
        self.routine = RoutineManager(self.config.get("routine"))
        self.task_scheduler = TaskScheduler()
        self.state_machine = StateMachine(initial="idle")
        self.locations = {"idle": "plaza", "eating": "dining_hall",
                          "sleeping": "home", "working": None}
        self.locations.update(self.config.get("locations", {}))
        self.position = self.locations["idle"]
        self._schedule: List[Dict] = []
        self._setup_default_states()

    # ---------- 状态机：转换条件 + 动作 ----------

    def _setup_default_states(self):
        sm = self.state_machine
        sm.add_transition("sleeping", "idle",
                          lambda c: c["is_day"] and not c["tired"])
        sm.add_transition("eating", "idle",
                          lambda c: not c["hungry"])
        sm.add_transition("working", "idle",
                          lambda c: c["current_task"] is None)
        sm.add_transition("working", "eating",
                          lambda c: c["hunger"] >= 90.0)
        sm.add_transition("idle", "sleeping",
                          lambda c: c["tired"] or (c["is_night"] and c["fatigue"] > 30.0))
        sm.add_transition("idle", "eating",
                          lambda c: c["hungry"])
        sm.add_transition("idle", "working",
                          lambda c: c["current_task"] is not None)

        sm.set_action("idle", lambda c: "wait")
        sm.set_action("working",
                      lambda c: "work:%s" % (c["current_task"] or "none"))
        sm.set_action("eating", lambda c: self._do_eat())
        sm.set_action("sleeping", lambda c: "sleep")

    def _do_eat(self) -> str:
        self.routine.eat()
        return "eat"

    # ---------- 日程规划：时间轴 + 优先级 ----------

    def plan_schedule(self, tasks: Optional[List] = None,
                      now: Optional[float] = None) -> List[Dict]:
        """
        把任务排上时间轴：高优先级先排，满足依赖与最早开始时间，
        单资源时间轴保证同一时间只做一件事（无冲突）。
        返回按开始时间排序的规划条目列表。
        """
        if tasks is None:
            task_list = list(self.task_scheduler.tasks.values())
        else:
            task_list = [_coerce_task(t) for t in tasks]
        if now is None:
            now = self.time_manager.time_of_day

        remaining = {t.id: t for t in task_list}
        end_times: Dict[str, float] = {}
        cursor = float(now)
        timeline: List[Dict] = []

        while remaining:
            ready = [t for t in remaining.values()
                     if all(d in end_times for d in t.dependencies)]
            if not ready:
                raise ValueError("任务依赖存在循环，无法规划日程")
            # 优先级高的先排；同级按时间轴（最早开始时间）先后
            ready.sort(key=lambda t: (-t.priority, t.earliest_start, t.id))
            task = ready[0]
            dep_end = max([end_times[d] for d in task.dependencies],
                          default=float(now))
            start = max(float(now), task.earliest_start, dep_end, cursor)
            end = start + task.duration
            timeline.append({
                "task_id": task.id,
                "name": task.name,
                "location": task.location,
                "priority": task.priority,
                "start": start,
                "end": end,
                "dependencies": list(task.dependencies),
                "on_time": end <= task.latest_end,
            })
            end_times[task.id] = end
            cursor = end  # 冲突处理：时间轴不重叠
            del remaining[task.id]

        timeline.sort(key=lambda e: (e["start"], -e["priority"]))
        self._schedule = timeline
        return timeline

    @property
    def schedule(self) -> List[Dict]:
        return list(self._schedule)

    # ---------- 便捷接口 ----------

    def add_task(self, task: Any) -> Task:
        return self.task_scheduler.add_task(task)

    @property
    def state(self) -> str:
        return self.state_machine.state

    @property
    def current_task(self) -> Optional[Task]:
        return self.task_scheduler.current

    @property
    def history(self) -> List[Dict]:
        return list(self._history)

    def _context(self) -> Dict:
        return {
            "time": self.time_manager.time_of_day,
            "day": self.time_manager.day,
            "season": self.time_manager.season,
            "is_day": self.time_manager.is_day(),
            "is_night": self.time_manager.is_night(),
            "hunger": self.routine.hunger,
            "fatigue": self.routine.fatigue,
            "hungry": self.routine.hungry,
            "tired": self.routine.tired,
            "current_task": self.task_scheduler.current_id,
        }

    def _update_position(self):
        state = self.state_machine.state
        if state == "working":
            task = self.task_scheduler.current
            self.position = task.location if task else self.locations["idle"]
        else:
            loc = self.locations.get(state)
            if loc:
                self.position = loc

    def get_status(self) -> Dict:
        return {
            "time": self.time_manager(),
            "routine": self.routine(),
            "scheduler": self.task_scheduler(),
            "state": self.state_machine.state,
            "position": self.position,
        }

    # ---------- 主循环 ----------

    def update(self, dt: float):
        dt_hours = float(dt) * self.time_scale
        self.time_manager.advance(dt_hours)
        sleeping = self.state_machine.state == "sleeping"
        self.routine.update(dt_hours, sleeping=sleeping)
        if self.state_machine.state == "working":
            self.task_scheduler.update(dt_hours)
        if self.state_machine.state == "idle":
            self.task_scheduler.start_next(self.time_manager.time_of_day)
        action = self.state_machine.update(self._context())
        self._update_position()
        self._history.append({
            "day": self.time_manager.day,
            "time": self.time_manager.time_of_day,
            "season": self.time_manager.season,
            "state": self.state_machine.state,
            "position": self.position,
            "action": action,
        })
        return action

    def reset(self):
        self._state = {}
        self._history = []
        self.time_manager = TimeManager(self.config.get("time"))
        self.routine = RoutineManager(self.config.get("routine"))
        self.task_scheduler = TaskScheduler()
        self.state_machine = StateMachine(initial="idle")
        self.position = self.locations["idle"]
        self._schedule = []
        self._setup_default_states()
