import time

from cinegatto.youtube_gate import YouTubeGate


class TestYouTubeGate:
    def test_blocks_after_threshold_failures(self):
        gate = YouTubeGate(threshold=3, cooldown_sec=60)
        gate.record_failure()
        gate.record_failure()
        assert not gate.is_blocked()
        gate.record_failure()  # 3rd = threshold
        assert gate.is_blocked()

    def test_unblocks_after_cooldown(self):
        gate = YouTubeGate(threshold=1, cooldown_sec=0.1)
        gate.record_failure()
        assert gate.is_blocked()
        time.sleep(0.15)
        assert not gate.is_blocked()

    def test_time_remaining(self):
        gate = YouTubeGate(threshold=1, cooldown_sec=10)
        gate.record_failure()
        remaining = gate.time_remaining()
        assert 9 < remaining <= 10

    def test_record_success_resets(self):
        gate = YouTubeGate(threshold=2, cooldown_sec=60)
        gate.record_failure()
        gate.record_success()
        gate.record_failure()  # only 1 since reset
        assert not gate.is_blocked()

    def test_not_blocked_initially(self):
        gate = YouTubeGate()
        assert not gate.is_blocked()
        assert gate.time_remaining() == 0
