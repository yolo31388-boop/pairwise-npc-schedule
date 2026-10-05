"""
游戏内NPC日程规划与时间管理系统 - 核心模块

包含五个子系统:
  - plan_schedule()  日程规划: 带时间轴(start/end)与优先级, 同一时刻高优先级活动胜出
  - time_manager()   时间管理: 昼夜周期 + 四季轮换, 季节不同日出日落不同
  - routine()        作息安排: 饥饿/精力需求衰减, 吃饭/睡觉恢复状态
  - task_scheduler() 任务调度: 依赖关系前置 + 同一时刻只能执行一个任务(冲突)
  - state_machine()  状态机:  转换条件驱动切换, 每个状态绑定动作
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
import math
import time


SEASONS: Tuple[str, ...] = ("spring", "summer", "autumn", "winter")

# 各季节的 (日出, 日落) 小时数; 夏季白天长, 冬季白天短
SEASON_DAYLIGHT: Dict[str, Tuple[float, float]] = {
    "spring": (6.0, 18.0),
    "summer": (5.0, 19.0),
    "autumn": (6.0, 18.0),
    "winter": (7.0, 17.0),
}

MEAL_NAMES = {"breakfast", "lunch", "dinner", "eat"}

# 默认一日时间轴: (活动, 开始, 结束, 优先级, 地点); 睡眠跨午夜
DEFAULT_ACTIVITIES: Tuple[Dict, ...] = (
    {"name": "sleep", "start": 22.0, "end": 30.0, "priority": 10, "location": "home"},
    {"name": "breakfast", "start": 7.0, "end": 8.0, "priority": 8, "location": "home"},
    {"name": "work", "start": 9.0, "end": 12.0, "priority": 5, "location": "workplace"},
    {"name": "lunch", "start": 12.0, "end": 13.0, "priority": 8, "location": "tavern"},
    {"name": "work", "start": 13.0, "end": 18.0, "priority": 5, "location": "workplace"},
    {"name": "dinner", "start": 18.0, "end": 19.0, "priority": 8, "location": "tavern"},
    {"name": "leisure", "start": 19.0, "end": 22.0, "priority": 3, "location": "plaza"},
)


@dataclass
class Config:
    start_hour: float = 8.0
    start_day: int = 0
    days_per_season: int = 30
    home: str = "home"
    hunger_rate: float = 100.0 / 16.0
    energy_rate: float = 100.0 / 16.0
    eat_threshold: float = 40.0
    sleep_threshold: float = 30.0
    eat_recovery: float = 50.0
    sleep_recovery: float = 100.0 / 8.0


class NPCSchedule:
    HOURS_PER_DAY = 24.0

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self._state = {}
        self._history: List[Dict] = []

        # --- 时间管理 ---
        self.hour: float = float(self.config.get("start_hour", Config.start_hour))
        self.day: int = int(self.config.get("start_day", Config.start_day))
        self.days_per_season: int = int(
            self.config.get("days_per_season", Config.days_per_season)
        )
        self.season_index: int = (
            (self.day // self.days_per_season) % len(SEASONS)
            if self.days_per_season > 0
            else 0
        )

        # --- NPC 自身 ---
        self.home: str = self.config.get("home", Config.home)
        self.position: str = self.home

        # --- 作息需求 (0~100) ---
        self.needs: Dict[str, float] = {"hunger": 100.0, "energy": 100.0}
        self.hunger_rate = float(self.config.get("hunger_rate", Config.hunger_rate))
        self.energy_rate = float(self.config.get("energy_rate", Config.energy_rate))
        self.eat_threshold = float(self.config.get("eat_threshold", Config.eat_threshold))
        self.sleep_threshold = float(self.config.get("sleep_threshold", Config.sleep_threshold))
        self.eat_recovery = float(self.config.get("eat_recovery", Config.eat_recovery))
        self.sleep_recovery = float(self.config.get("sleep_recovery", Config.sleep_recovery))

        # --- 日程规划 ---
        self.schedule: List[Dict] = []

        # --- 任务调度 ---
        self.tasks: Dict[str, Dict] = {}
        self.current_task: Optional[str] = None

        # --- 状态机 ---
        self.state: str = "idle"
        self._sm_states: Dict[str, Optional[Callable]] = {}
        self._sm_transitions: List[Dict] = []

    # ============================================================
    # 1) 日程规划: 时间轴 + 优先级
    # ============================================================
    def plan_schedule(self, activities: Optional[List[Dict]] = None) -> List[Dict]:
        """根据活动列表生成按时间轴排序的日程; 同一时刻高优先级活动胜出。"""
        source = DEFAULT_ACTIVITIES if activities is None else activities
        entries: List[Dict] = []
        for act in source:
            start = float(act["start"])
            end = float(act["end"])
            if end <= start:
                raise ValueError("活动结束时间必须晚于开始时间")
            entries.append(
                {
                    "name": act["name"],
                    "start": start % self.HOURS_PER_DAY,
                    "end": end,
                    "priority": int(act.get("priority", 0)),
                    "location": act.get("location", self.home),
                }
            )
        # 时间轴排序: 开始时间升序, 同时开始时优先级高的在前
        entries.sort(key=lambda e: (e["start"], -e["priority"]))
        self.schedule = entries
        return [dict(e) for e in entries]

    def _covers(self, entry: Dict, hour: float) -> bool:
        start, end = entry["start"], entry["end"]
        if start < end:
            return start <= hour < end
        # 跨午夜的活动 (如 22~30)
        return hour >= start or hour < (end % self.HOURS_PER_DAY)

    def current_activity(self, hour: Optional[float] = None) -> Optional[Dict]:
        """返回当前时间点应执行的活动; 多个活动重叠时取优先级最高者。"""
        if not self.schedule:
            return None
        hour = self.hour if hour is None else hour % self.HOURS_PER_DAY
        best: Optional[Dict] = None
        for entry in self.schedule:
            if self._covers(entry, hour) and (
                best is None or entry["priority"] > best["priority"]
            ):
                best = entry
        return dict(best) if best is not None else None

    # ============================================================
    # 2) 时间管理: 昼夜周期 + 季节变化
    # ============================================================
    def time_manager(self, dt: float = 0.0) -> Dict:
        """推进游戏时间 dt 小时, 返回当前时刻、昼夜与季节信息。"""
        self.hour += float(dt)
        while self.hour >= self.HOURS_PER_DAY:
            self.hour -= self.HOURS_PER_DAY
            self.day += 1
        if self.days_per_season > 0:
            self.season_index = (self.day // self.days_per_season) % len(SEASONS)
        season = SEASONS[self.season_index]
        sunrise, sunset = SEASON_DAYLIGHT[season]
        is_day = sunrise <= self.hour < sunset
        return {
            "hour": self.hour,
            "day": self.day,
            "season": season,
            "sunrise": sunrise,
            "sunset": sunset,
            "is_day": is_day,
            "is_night": not is_day,
        }

    # ============================================================
    # 3) 作息安排: 需求 + 恢复
    # ============================================================
    def required_action(self) -> Optional[str]:
        """根据当前需求给出必须优先处理的作息动作: 先困后饿。"""
        if self.needs["energy"] <= self.sleep_threshold:
            return "sleep"
        if self.needs["hunger"] <= self.eat_threshold:
            return "eat"
        return None

    def routine(self, dt: float = 0.0) -> Dict[str, float]:
        """推进需求变化: 活动时饥饿/精力衰减, 吃饭恢复饥饿, 睡觉恢复精力。"""
        dt = float(dt)
        activity = self.current_activity()
        act_name = activity["name"] if activity else None
        sleeping = act_name == "sleep" or self.state == "sleep"
        eating = act_name in MEAL_NAMES or self.state == "eat"

        self.needs["energy"] += (self.sleep_recovery if sleeping else -self.energy_rate) * dt
        self.needs["hunger"] += (self.eat_recovery if eating else -self.hunger_rate) * dt

        # 需求驱动: 困了必须睡觉, 饿了必须吃饭
        required = self.required_action()
        if required == "sleep" and self.state in ("idle", "eat"):
            self.state = "sleep"
        elif required == "eat" and self.state == "idle":
            self.state = "eat"

        for key in self.needs:
            self.needs[key] = max(0.0, min(100.0, self.needs[key]))
        return dict(self.needs)

    # ============================================================
    # 4) 任务调度: 依赖 + 冲突
    # ============================================================
    def add_task(
        self,
        name: str,
        duration: float,
        depends_on: Optional[List[str]] = None,
        priority: int = 0,
    ) -> Dict:
        """登记一个任务; depends_on 中的任务全部完成后该任务才允许开始。"""
        self.tasks[name] = {
            "name": name,
            "duration": float(duration),
            "depends_on": list(depends_on or []),
            "priority": int(priority),
            "progress": 0.0,
            "status": "pending",  # pending -> running -> completed
        }
        return self.tasks[name]

    def _deps_completed(self, task: Dict) -> bool:
        return all(
            self.tasks.get(dep, {}).get("status") == "completed"
            for dep in task["depends_on"]
        )

    def task_scheduler(self, dt: float = 0.0) -> Dict[str, str]:
        """调度执行任务: 同一时刻最多一个 running(冲突解决), 且只挑依赖已完成的任务。"""
        running = [t for t in self.tasks.values() if t["status"] == "running"]
        if not running:
            ready = [
                t
                for t in self.tasks.values()
                if t["status"] == "pending" and self._deps_completed(t)
            ]
            if ready:
                # 依赖就绪的任务中按优先级挑选, 其余继续等待(避免同一时刻并行)
                ready.sort(key=lambda t: (-t["priority"], t["name"]))
                chosen = ready[0]
                chosen["status"] = "running"
                self.current_task = chosen["name"]
                running = [chosen]

        for task in running:
            task["progress"] += float(dt)
            if task["progress"] >= task["duration"]:
                task["status"] = "completed"
                task["progress"] = task["duration"]
                if self.current_task == task["name"]:
                    self.current_task = None
        return {name: t["status"] for name, t in self.tasks.items()}

    # ============================================================
    # 5) 状态机: 转换条件 + 状态动作
    # ============================================================
    def state_machine(
        self,
        states: Optional[Dict[str, Optional[Callable]]] = None,
        transitions: Optional[List[Dict]] = None,
        initial: Optional[str] = None,
    ) -> str:
        """注册状态动作与带条件的转换, 可指定初始状态。"""
        if states:
            for name, action in states.items():
                self._sm_states[name] = action
        if transitions:
            for tr in transitions:
                self._sm_transitions.append(
                    {"from": tr["from"], "to": tr["to"], "condition": tr["condition"]}
                )
        if initial is not None:
            self.state = initial
        elif self._sm_states and self.state not in self._sm_states:
            self.state = next(iter(self._sm_states))
        return self.state

    def check_transitions(self) -> Optional[str]:
        """按注册顺序评估当前状态的转换条件, 第一个满足的条件触发切换。"""
        for tr in self._sm_transitions:
            if tr["from"] == self.state and tr["condition"](self):
                self.state = tr["to"]
                self._history.append(
                    {"event": "transition", "from": tr["from"], "to": tr["to"], "hour": self.hour}
                )
                return tr["to"]
        return None

    def run_action(self):
        """执行当前状态绑定的动作。"""
        action = self._sm_states.get(self.state)
        if callable(action):
            return action(self)
        return None

    # ============================================================
    # 统一推进
    # ============================================================
    def update(self, dt: float) -> Dict:
        """推进 dt 小时: 时间 -> 作息 -> 任务 -> 状态机 -> 日程位移。"""
        time_info = self.time_manager(dt)
        needs = self.routine(dt)
        task_status = self.task_scheduler(dt)
        if self._sm_states:
            self.check_transitions()
            self.run_action()

        activity = self.current_activity()
        if activity is not None:
            self.position = activity["location"]

        self._history.append(
            {
                "hour": self.hour,
                "day": self.day,
                "state": self.state,
                "position": self.position,
                "activity": activity["name"] if activity else None,
                "needs": dict(self.needs),
            }
        )
        return {
            "time": time_info,
            "needs": needs,
            "tasks": task_status,
            "state": self.state,
            "position": self.position,
            "activity": activity["name"] if activity else None,
        }

    def reset(self):
        self.__init__(self.config)
