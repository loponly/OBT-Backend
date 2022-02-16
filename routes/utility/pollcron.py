from datetime import timedelta, datetime, date
from typing import *
from result import Result, Ok, Err

# TODO: create fixed day offset function (do_on_day)
def implicit_resolution(a: timedelta):
    denominations = [timedelta(weeks=1), timedelta(days=1), timedelta(hours=1), timedelta(minutes=1), timedelta(seconds=1)]
    for native in denominations:
        w, a = divmod(a, native)
        if a.total_seconds() == 0: 
            return native

    return timedelta.resolution()

class TimedExecUtils:
    def __init__(self, db: dict, ignore_fail=True):
        self.pass_fail = ignore_fail
        self.db = db

    def do_once(unique_id: str, rollover: timedelta = timedelta(days=7)) -> Result[None,None]:
        """
        Returns `Ok()`, once and then `Err()` until the rollover specified was reached
        """
        if unique_id in self.db:
            if datetime.now() < self.db[unique_id]:
                return Err()

        self.db[unique_id] = datetime.now() + rollover
        # TODO: clean old unique_ids (expire?)
        return Ok()

    def do_once_each_with(when: Dict[datetime,Any], unique_id: str, rollover: timedelta = timedelta(days=7)) -> Result[Any,None]:
        """
        Returns Ok(when[t]) if t has been reached for the first time. 
        """
        for _dt in when:
            # Not yet reached
            if _dt > datetime.now():
                continue

            # Check if already ran
            # TODO: clean old unique_ids (expire?)
            res = self.do_once((unique_id, _dt), rollover=rollover)
            if res.is_ok():
                return Ok(when[_dt])

        return Err()
