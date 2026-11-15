"""
behavior.py — 行为节律（服务端风控唯一看得到的东西）

客户端特征可以抹，但服务端只认三条统计量：
  1) 在线时段分布      —— 真人集中在 18:00-01:00，且有断点
  2) 单次会话时长      —— 真人 40~150 分钟，超过 6 小时是机器特征
  3) 动作间隔分布      —— 真人是对数正态 + 长尾，机器人是窄峰

这个模块给出一个"日程计划器"和一个"节拍器"，把 ok-ww 的主循环
从"连续 12 小时恒定 APM"改成"人类作息的采样"。
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import random
import time


class Pacer:
    """动作间隔采样器：对数正态，带 2% 长尾（发呆/看手机）。"""

    def __init__(self, median: float = 0.45, sigma: float = 0.55, long_tail_p: float = 0.02,
                 long_tail_range=(1.5, 7.0), seed: int | None = None):
        self.median = median
        self.sigma = sigma
        self.long_tail_p = long_tail_p
        self.long_tail_range = long_tail_range
        self.rng = random.Random(seed)

    def next_interval(self) -> float:
        if self.rng.random() < self.long_tail_p:
            return self.rng.uniform(*self.long_tail_range)
        v = self.rng.lognormvariate(math.log(self.median), self.sigma)
        return min(v, self.median * 8)

    def wait(self) -> float:
        d = self.next_interval()
        time.sleep(d)
        return d


class SessionPlan:
    """
    一天的日程。默认假设玩家是学生/上班族：
      主会话 19:00±90min 开始，时长 lognormal(median=75min)
      30% 概率下午有一次短会话（12:30±40min，20~45min）
      7% 概率整晚不玩（现实里的缺席同样是反检测的一部分）
    """

    def __init__(self, seed: int | None = None, night_start: float = 19.0,
                 night_sigma_h: float = 1.5, median_min: float = 78.0,
                 max_min: float = 165.0, afternoon_p: float = 0.3,
                 skip_p: float = 0.07):
        self.rng = random.Random(seed)
        self.night_start = night_start
        self.night_sigma_h = night_sigma_h
        self.median_min = median_min
        self.max_min = max_min
        self.afternoon_p = afternoon_p
        self.skip_p = skip_p

    def plan(self, day: _dt.date | None = None) -> dict:
        day = day or _dt.date.today()
        if self.rng.random() < self.skip_p:
            return {"day": str(day), "sessions": [], "note": "rest day"}

        sessions = []
        # 主会话
        start_h = self.rng.gauss(self.night_start, self.night_sigma_h)
        start_h = max(11.0, min(23.5, start_h))
        dur = min(self.max_min, self.rng.lognormvariate(math.log(self.median_min), 0.55))
        sessions.append(self._mk(day, start_h, dur))

        # 下午短会话
        if self.rng.random() < self.afternoon_p:
            start_h = self.rng.gauss(12.9, 0.7)
            dur = self.rng.uniform(18, 46)
            sessions.append(self._mk(day, start_h, dur))

        sessions.sort(key=lambda s: s["start"])
        return {"day": str(day), "sessions": sessions}

    def _mk(self, day, start_h: float, dur_min: float) -> dict:
        """把一段会话切成 3~5 个子段，中间插休息（真人不会一动不动）。"""
        hh = int(start_h)
        mm = int((start_h - hh) * 60)
        begin = _dt.datetime.combine(day, _dt.time(hh % 24, max(0, min(59, mm))))
        chunks = []
        remain = dur_min
        t = begin
        while remain > 4:
            run = min(remain, self.rng.uniform(14, 52))
            chunks.append({"start": t.isoformat(timespec="minutes"),
                           "run_min": round(run, 1)})
            t = t + _dt.timedelta(minutes=run + self.rng.uniform(2.5, 13.0))
            remain -= run
            if remain < 8:
                break
        return {"start": begin.isoformat(timespec="minutes"),
                "total_min": round(dur_min, 1),
                "chunks": chunks,
                "end": t.isoformat(timespec="minutes")}

    def should_run_now(self, now: _dt.datetime | None = None) -> bool:
        now = now or _dt.datetime.now()
        p = self.plan(now.date())
        for s in p["sessions"]:
            b = _dt.datetime.fromisoformat(s["start"])
            e = b + _dt.timedelta(minutes=s["total_min"])
            if b - _dt.timedelta(minutes=12) <= now <= e:
                return True
        return False

    def next_start(self, now: _dt.datetime | None = None) -> _dt.datetime:
        now = now or _dt.datetime.now()
        p = self.plan(now.date())
        for s in p["sessions"]:
            b = _dt.datetime.fromisoformat(s["start"])
            if b > now:
                return b
        return _dt.datetime.combine(now.date() + _dt.timedelta(days=1), _dt.time(19, 0))


def write_schedule(path: str, days: int = 7, seed: int | None = None) -> dict:
    plan = SessionPlan(seed)
    today = _dt.date.today()
    out = {"generated": _dt.datetime.now().isoformat(timespec="seconds"),
           "days": [plan.plan(today + _dt.timedelta(days=i)) for i in range(days)]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


if __name__ == "__main__":
    import sys
    p = write_schedule(sys.argv[1] if len(sys.argv) > 1 else "schedule.json")
    for d in p["days"]:
        print(d["day"], [(s["start"], round(s["total_min"])) for s in d["sessions"]])
