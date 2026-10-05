"""
游戏内NPC日程规划与时间管理系统 - 验收测试
运行方式：python -m pytest tests/test_npc_schedule.py -q
共 15 个测试用例
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.npc_schedule import NPCSchedule


class TestNPCSchedule:
    def setup_method(self):
        self.system = NPCSchedule()

    # ---------- 日程规划: 时间轴 ----------
    def test_case_01(self):
        timeline = self.system.plan_schedule()
        assert len(timeline) > 0
        starts = [e["start"] for e in timeline]
        assert starts == sorted(starts), "日程必须按时间轴排序"
        for entry in timeline:
            assert 0.0 <= entry["start"] < 24.0
            assert entry["end"] > entry["start"]
            assert "location" in entry and entry["location"]

    # ---------- 日程规划: 优先级 ----------
    def test_case_02(self):
        self.system.plan_schedule([
            {"name": "idle_walk", "start": 8.0, "end": 20.0, "priority": 1, "location": "street"},
            {"name": "guard", "start": 10.0, "end": 12.0, "priority": 9, "location": "gate"},
        ])
        act = self.system.current_activity(11.0)
        assert act["name"] == "guard", "重叠时段必须执行高优先级活动"
        act = self.system.current_activity(15.0)
        assert act["name"] == "idle_walk"

    # ---------- 日程规划: NPC 不再原地发呆 ----------
    def test_case_03(self):
        self.system.plan_schedule()
        positions = set()
        for _ in range(16):  # 从 8 点推进到 24 点
            info = self.system.update(1.0)
            positions.add(info["position"])
        assert len(positions) > 1, "NPC 必须随日程在不同地点之间移动"

    # ---------- 时间管理: 昼夜周期 ----------
    def test_case_04(self):
        self.system.time_manager(4.0)  # 8 -> 12 点
        noon = self.system.time_manager(0.0)
        assert noon["is_day"] and not noon["is_night"]
        self.system.time_manager(11.0)  # 12 -> 23 点
        night = self.system.time_manager(0.0)
        assert night["is_night"] and not night["is_day"]
        assert night["hour"] == pytest.approx(23.0)

    # ---------- 时间管理: 季节变化 ----------
    def test_case_05(self):
        assert self.system.time_manager(0.0)["season"] == "spring"
        self.system.time_manager(24.0 * 30)  # 推进一个季节
        info = self.system.time_manager(0.0)
        assert info["season"] == "summer"
        assert info["day"] == 30

    # ---------- 时间管理: 不同季节作息不同 ----------
    def test_case_06(self):
        summer = self.system.time_manager(24.0 * 30)   # 推进到夏季
        winter = self.system.time_manager(24.0 * 60)   # 推进到冬季
        assert summer["season"] == "summer"
        assert winter["season"] == "winter"
        summer_len = summer["sunset"] - summer["sunrise"]
        winter_len = winter["sunset"] - winter["sunrise"]
        assert summer_len > winter_len, "夏季白昼必须比冬季长"

    # ---------- 作息安排: 需求衰减 ----------
    def test_case_07(self):
        self.system.plan_schedule()
        before = dict(self.system.needs)
        self.system.time_manager(2.0)  # 8 -> 10 点, 工作时段
        after = self.system.routine(2.0)
        assert after["hunger"] < before["hunger"], "活动时饥饿度必须下降"
        assert after["energy"] < before["energy"], "活动时精力必须下降"

    # ---------- 作息安排: 吃饭睡觉恢复 ----------
    def test_case_08(self):
        self.system.needs["energy"] = 20.0
        self.system.needs["hunger"] = 20.0
        self.system.state = "sleep"
        self.system.routine(4.0)
        assert self.system.needs["energy"] > 20.0, "睡觉必须恢复精力"
        hunger_after_sleep = self.system.needs["hunger"]
        self.system.state = "eat"
        self.system.routine(1.0)
        assert self.system.needs["hunger"] > hunger_after_sleep, "吃饭必须恢复饥饿度"

    # ---------- 作息安排: 需求驱动行为 ----------
    def test_case_09(self):
        self.system.needs["energy"] = 10.0
        assert self.system.required_action() == "sleep", "困了必须睡觉"
        self.system.needs["energy"] = 100.0
        self.system.needs["hunger"] = 10.0
        assert self.system.required_action() == "eat", "饿了必须吃饭"
        self.system.needs["hunger"] = 100.0
        assert self.system.required_action() is None

    # ---------- 任务调度: 任务会执行 ----------
    def test_case_10(self):
        self.system.add_task("chop_wood", duration=2.0)
        status = self.system.task_scheduler(1.0)
        assert status["chop_wood"] == "running", "任务必须被调度执行"
        status = self.system.task_scheduler(1.0)
        assert status["chop_wood"] == "completed", "任务进度满后必须完成"

    # ---------- 任务调度: 依赖 ----------
    def test_case_11(self):
        self.system.add_task("gather", duration=1.0)
        self.system.add_task("cook", duration=1.0, depends_on=["gather"])
        self.system.task_scheduler(0.5)
        assert self.system.tasks["cook"]["status"] == "pending", "依赖未完成时不能开始"
        self.system.task_scheduler(0.5)  # gather 完成
        assert self.system.tasks["gather"]["status"] == "completed"
        self.system.task_scheduler(0.5)
        assert self.system.tasks["cook"]["status"] == "running", "依赖完成后才能开始"

    # ---------- 任务调度: 冲突 ----------
    def test_case_12(self):
        self.system.add_task("task_a", duration=5.0, priority=1)
        self.system.add_task("task_b", duration=5.0, priority=2)
        status = self.system.task_scheduler(0.1)
        running = [name for name, s in status.items() if s == "running"]
        assert len(running) == 1, "同一时刻只能执行一个任务"
        assert running[0] == "task_b", "冲突时优先执行高优先级任务"

    # ---------- 状态机: 转换条件 ----------
    def test_case_13(self):
        self.system.state_machine(
            states={"idle": None, "work": None},
            transitions=[
                {"from": "idle", "to": "work", "condition": lambda s: s.hour >= 9.0},
            ],
            initial="idle",
        )
        assert self.system.check_transitions() is None, "条件不满足时不能切换"
        self.system.time_manager(2.0)  # 8 -> 10 点
        assert self.system.check_transitions() == "work", "条件满足时必须切换状态"
        assert self.system.state == "work"

    # ---------- 状态机: 状态动作 ----------
    def test_case_14(self):
        log = []
        self.system.state_machine(
            states={
                "eat": lambda s: log.append(("eat", round(s.hour, 2))),
                "idle": None,
            },
            initial="eat",
        )
        self.system.run_action()
        self.system.run_action()
        assert len(log) == 2 and all(entry[0] == "eat" for entry in log), \
            "每个状态必须执行其绑定的动作"

    # ---------- 整合: update 推进 + reset ----------
    def test_case_15(self):
        self.system.plan_schedule()
        self.system.add_task("daily_job", duration=3.0)
        states_seen = set()
        for _ in range(6):
            info = self.system.update(1.0)
            states_seen.add(info["state"])
        assert self.system.hour != 8.0 or self.system.day != 0
        assert self.system.tasks["daily_job"]["status"] == "completed"
        assert len(self.system._history) == 6
        self.system.reset()
        assert self.system.hour == 8.0 and self.system.day == 0
        assert self.system.needs == {"hunger": 100.0, "energy": 100.0}
        assert self.system._history == [] and self.system.tasks == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
