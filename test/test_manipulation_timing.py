import unittest

from x2_operator_panel.manipulation_timing import ManipulationTiming


EXECUTING = 2
TERMINAL = {4, 5, 6}


class ManipulationTimingTest(unittest.TestCase):
    def test_separates_task_wall_time_from_controller_motion(self):
        timer = ManipulationTiming()
        timer.observe_task({"task_id": "task-a", "status": "running",
                            "phase": "", "attempt": 0}, 10.0)
        timer.observe_controller("goal-1", EXECUTING, 12.0, EXECUTING, TERMINAL)
        timer.observe_controller("goal-1", EXECUTING, 13.0, EXECUTING, TERMINAL)
        timer.observe_controller("goal-1", 4, 15.0, EXECUTING, TERMINAL)
        timer.observe_controller("goal-1", 4, 16.0, EXECUTING, TERMINAL)
        timer.observe_task({"task_id": "task-a", "status": "paused"}, 16.0)
        timer.observe_controller("goal-2", EXECUTING, 20.0, EXECUTING, TERMINAL)
        self.assertEqual(timer.snapshot(22.0)["controller_execution_sec"], 5.0)
        timer.observe_controller("goal-2", 4, 24.0, EXECUTING, TERMINAL)
        timer.observe_task({"task_id": "task-a", "status": "completed"}, 25.0)
        self.assertEqual(timer.snapshot(40.0), {
            "task_elapsed_sec": 15.0,
            "controller_execution_sec": 7.0,
            "controller_goal_count": 2,
            "timing_partial": False,
        })

    def test_historical_terminal_and_new_task_reset(self):
        timer = ManipulationTiming()
        timer.observe_task({"task_id": "old", "status": "completed"}, 1.0)
        self.assertIsNone(timer.snapshot(2.0)["task_elapsed_sec"])
        timer.observe_task({"task_id": "old", "status": "retrying",
                            "phase": "carry", "attempt": 2}, 3.0)
        self.assertTrue(timer.snapshot(4.0)["timing_partial"])
        timer.observe_controller("old-goal", EXECUTING, 4.0, EXECUTING, TERMINAL)
        timer.observe_task({"task_id": "new", "status": "running",
                            "phase": "", "attempt": 0}, 5.0)
        timer.observe_controller("old-goal", 4, 6.0, EXECUTING, TERMINAL)
        self.assertEqual(timer.snapshot(7.0)["controller_execution_sec"], 0.0)
        self.assertEqual(timer.snapshot(7.0)["controller_goal_count"], 0)
        self.assertFalse(timer.snapshot(7.0)["timing_partial"])

    def test_late_controller_terminal_is_capped_at_task_end(self):
        timer = ManipulationTiming()
        timer.observe_task({"task_id": "task", "status": "running",
                            "phase": "", "attempt": 0}, 1.0)
        timer.observe_controller("goal", EXECUTING, 2.0, EXECUTING, TERMINAL)
        timer.observe_task({"task_id": "task", "status": "canceled"}, 4.0)
        self.assertEqual(timer.snapshot(10.0)["controller_execution_sec"], 2.0)
        timer.observe_controller("goal", 5, 11.0, EXECUTING, TERMINAL)
        self.assertEqual(timer.snapshot(12.0)["controller_execution_sec"], 2.0)


if __name__ == "__main__":
    unittest.main()
