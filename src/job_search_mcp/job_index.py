"""A small in-memory memory of recently returned jobs, so get_job_details can
look up an id from a previous search. Nothing is written to disk."""

from __future__ import annotations

from collections import OrderedDict

from job_search_mcp.models import Job

MAX_JOBS = 3000


class JobIndex:
    def __init__(self, max_jobs: int = MAX_JOBS) -> None:
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._max = max_jobs

    def put_many(self, jobs: list[Job]) -> None:
        for job in jobs:
            self._jobs[job.id] = job
            self._jobs.move_to_end(job.id)
        while len(self._jobs) > self._max:
            self._jobs.popitem(last=False)  # forget the oldest

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def __len__(self) -> int:
        return len(self._jobs)
