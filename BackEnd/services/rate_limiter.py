import threading
import datetime

class fixed_window:
    def __init__(self,T, N):
        self.limit = N
        self.Tokens = 0
        self.window_length = T
        self.lock = threading.Lock()
        tm = datetime.datetime.now()
        self.start_time = self.time_floor(tm)

    def is_allowed(self, route):
        
        with self.lock:
            if self.Tokens < self.limit:
                return True
            elif datetime.datetime.now() - self.start_time > datetime.timedelta(seconds=self.window_length):
                self.start_time = self.time_floor(datetime.datetime.now())
                self.Tokens = 0
                return True
            return False

    def time_floor(self, tm):
        return tm - datetime.timedelta(minutes=tm.minute % 1,
                                        seconds=tm.second,
                                        microseconds=tm.microsecond)

    def increment(self):
        with self.lock:
            if self.Tokens < self.limit:
                self.Tokens += 1
                
